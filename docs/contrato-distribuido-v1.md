# Contrato interno distribuido v1 — Issue #275

- Fecha: 2026-11-12. Estado: modelos #276 y servicio de nodo #277 implementados.
- Responsables: Mauricio (modelos y servidor de nodo), Oswaldo (cliente y coordinador).
- Alcance y restricciones: [arquitectura distribuida](arquitectura-distribuida.md).

Las palabras «debe» y «rechaza» son requisitos de implementación de v1. Este
documento define las interfaces normativas; el servicio implementado se describe
en [Operaciones sobre fragmentos](operaciones-fragmentos.md). El cliente del
coordinador sigue correspondiendo a #278.

## 1. Transporte, identidad y versiones

- HTTP/JSON UTF-8; solicitudes POST con `Content-Type: application/json`.
- Prefijo interno `/internal/v1`. El cliente no sigue redirecciones ni reintenta
  peticiones automáticamente. Timeout de conexión: 3 s; lectura/escritura: 30 s;
  pool: 3 s. Son valores iniciales configurables, no garantías de ejecución.
- `protocol_version` es el entero `1` en todos los documentos y sobres.
- `catalog_version` es un entero positivo, global al clúster. Cambiar metadatos
  o ubicación de datos exige incrementarlo; no es un contador de filas.
- La v1 exige igualdad exacta de ambas versiones. No negocia downgrade.
- `node_id`: identificador estable y único del nodo, independiente de la URL.
  `fragment_id`: único en todo el catálogo. Ambos cumplen
  `[a-z][a-z0-9_]{0,63}` y distinguen mayúsculas (no se aceptan mayúsculas).
- `table` y nombres de columnas conservan los identificadores del catálogo
  local; no se cambia su capitalización en el transporte.
- `request_id`: UUID canónico en minúsculas, nuevo por petición HTTP. Se devuelve
  sin cambios. No otorga idempotencia. El coordinador puede asociar varias
  peticiones hijas a una consulta en sus logs, sin un campo nuevo de protocolo.
- Campos desconocidos en solicitudes o configuración se rechazan; campos
  opcionales se enumeran expresamente. El cliente valida versiones y campos
  requeridos de respuestas y puede ignorar campos adicionales de diagnóstico.
- No se transmiten AST Python, pickle, páginas, RID ni handles C++.

## 2. Catálogo distribuido

Ejemplo completo de configuración para una tabla fragmentada:

```json
{
  "protocol_version": 1,
  "catalog_version": 1,
  "nodes": [
    {"node_id": "n1", "base_url": "http://n1:8000"},
    {"node_id": "n2", "base_url": "http://n2:8000"}
  ],
  "tables": [
    {
      "name": "items",
      "mode": "range",
      "storage": "heap",
      "primary_key": "id",
      "columns": [
        {"name": "id", "type": "INT", "size": null},
        {"name": "nombre", "type": "VARCHAR", "size": 80}
      ],
      "indexes": [],
      "fragments": [
        {"fragment_id": "items_f0", "node_id": "n1", "lower": null, "upper": 10000},
        {"fragment_id": "items_f1", "node_id": "n2", "lower": 10000, "upper": null}
      ]
    }
  ]
}
```

Todos los campos mostrados son obligatorios. `base_url` no tiene ruta ni barra
final y usa HTTP o HTTPS. Una tabla `single` tiene exactamente un fragmento con
ambos límites `null`; su PK puede ser cualquier tipo que admita el motor local.
`range` requiere PK INT y `storage: "heap"`. `single` también admite
`sequential` y `bplus_clustered`. La tabla física tiene el mismo nombre lógico;
no hay alias ni más de un fragmento de la misma tabla en un nodo en v1.

`columns` conserva el orden físico. `size` es la capacidad en bytes de VARCHAR,
la dimensión de VECTOR y `null` para otros tipos. `primary_key` referencia una
columna existente. `indexes` contiene objetos con los campos `name`, `column`,
`structure`, `supports_range`, con la misma forma de `IndexInfo` en
[`engine/api/schemas.py`](../engine/api/schemas.py). No incluye rutas de archivos
ni sirve como instrucción de creación. La configuración de vocabularios,
tokenización y parámetros de índices sigue siendo local en las tablas `single`.
FULLTEXT (`inverted_index`) no se admite en tablas `range` v1.

El nodo compara esquema, organización e índices declarados con su catálogo
físico al arrancar. Datos fuera de rango también deben verificarse antes de
habilitarlo. Si no coinciden, no entra en estado `ready`.

### Pertenencia y selección

Para una clave INT `k`, pertenece a un fragmento si:

```text
(lower es null o lower <= k) y (upper es null o k < upper)
```

Los límites no nulos son enteros int32 (bool no cuenta como entero). Los rangos
ordenados deben comenzar con `lower: null`, terminar con `upper: null` y tener
fronteras adyacentes iguales, sin intervalos vacíos ni repetidos. Los IDs de
nodos deben existir. Una tabla `range` tiene al menos dos fragmentos y ningún
fragmento puede representar un conjunto vacío de claves INT válidas. Las PK
INT válidas están entre -2147483648 y 2147483647.
`null` en un límite significa infinito; nunca una clave nula.

`owner_for_key(items, 9999)` devuelve `items_f0`;
`owner_for_key(items, 10000)` devuelve `items_f1`.
Para comparaciones de PK, seleccionar los rangos que puedan contener una clave
que cumpla el predicado. No sumar/restar uno al valor comparado, para evitar
desbordamientos en los extremos. Sin restricción aprovechable, seleccionar
todos los fragmentos. Un conjunto vacío permite devolver una lectura vacía
tipada sin contactar nodos.

## 3. Endpoints internos

| Método y ruta | Entrada | Resultado |
|---|---|---|
| `GET /internal/v1/health` | Sin cuerpo | Identidad, versión y disponibilidad; 200 si ready, 503 si no. |
| `POST /internal/v1/catalog` | Sobre base | Descripción de tablas y fragmentos alojados, incluidos conteos actuales. |
| `POST /internal/v1/fragments/{fragment_id}/execute` | Sobre base + `table`, `sql` | Ejecución completa de una operación admitida en ese fragmento. |
| `POST /internal/v1/fragments/{fragment_id}/scan` | Sobre base + `table`, `sql` | Lectura de filas completas filtradas, para combinación global. |

El sobre base contiene exactamente `protocol_version`, `catalog_version` y
`request_id`. No hay endpoints internos de creación de fragmentos, DDL,
transacciones, modificación de topología ni transferencia de páginas.

`execute` acepta una sola sentencia. En tablas `range`: INSERT de una fila o
DELETE por igualdad de PK. En tablas `single`: INSERT, DELETE, SELECT o EXPLAIN
de una sola tabla, siempre que el motor los admita. No acepta lotes, JOIN,
DDL, BEGIN/END ni UPDATE SQL. El método `TableFile.update()` del núcleo no implica
que exista UPDATE en el parser SQL.

`scan` solo se admite para tablas `range`. Su SQL debe ser `SELECT * FROM tabla`
con un WHERE escalar opcional admitido en la matriz de arquitectura, sin alias,
JOIN, orden, límite, score ni agregación. El nodo comprueba la forma del AST;
no confía en el coordinador para restringirlo. Devuelve columnas en orden físico.

Oswaldo genera el SELECT de `scan` desde el AST validado, no mediante sustitución
de cadenas sobre SQL. Debe conservar el significado y el escape de los literales.
Mauricio valida y ejecuta ese SELECT con el motor existente. Esta separación
evita serializar planes o desarrollar un segundo evaluador de predicados.

En todas las peticiones de fragmento, la tabla SQL debe coincidir con `table`
y con el fragmento de la ruta. El nodo valida versión, identidad, forma admitida
y pertenencia de la PK de una escritura **antes de mutar**. No redirige ni
reenvía una petición al propietario correcto.

### Salud

```json
{
  "protocol_version": 1,
  "catalog_version": 1,
  "node_id": "n1",
  "status": "ready",
  "fragment_ids": ["items_f0"],
  "reason": null
}
```

`status` es `ready` o `not_ready`; `reason` es `null` o texto diagnóstico.
Salud no lleva `request_id`. Estar ready no garantiza que una operación posterior
termine. Una configuración ilegible puede impedir arrancar el servidor; en ese
caso habrá un fallo de conexión en lugar de este JSON.

### Catálogo operativo

Petición a `/internal/v1/catalog`:

```json
{"protocol_version":1,"catalog_version":1,"request_id":"b928fbbe-3214-4c2b-90c8-6c79540d5d10"}
```

Respuesta 200:

```json
{
  "protocol_version": 1,
  "catalog_version": 1,
  "request_id": "b928fbbe-3214-4c2b-90c8-6c79540d5d10",
  "node_id": "n1",
  "fragments": [
    {
      "fragment_id": "items_f0",
      "table_info": {
        "name": "items", "storage": "heap",
        "columns": [
          {"name":"id","type":"INT","size":null,"is_primary_key":true},
          {"name":"nombre","type":"VARCHAR","size":80,"is_primary_key":false}
        ],
        "indexes": [], "record_count": 1
      }
    }
  ]
}
```

`table_info` usa el modelo público `TableInfo`; incluye todos los fragmentos
alojados, sin exponer rutas físicas. El coordinador contrasta esta respuesta con
la configuración estática; no descubre ni incorpora automáticamente fragmentos.

## 4. Ejemplos de ejecución y combinación

Inserción a `POST /internal/v1/fragments/items_f0/execute`:

```json
{
  "protocol_version": 1,
  "catalog_version": 1,
  "request_id": "de783f4e-a813-4fa1-86ec-b7e3abc40291",
  "table": "items",
  "sql": "INSERT INTO items VALUES (42, 'Quipu');"
}
```

Respuesta 200:

```json
{
  "protocol_version": 1,
  "catalog_version": 1,
  "request_id": "de783f4e-a813-4fa1-86ec-b7e3abc40291",
  "node_id": "n1",
  "fragment_id": "items_f0",
  "result": {
    "columns": [], "column_types": [], "rows": [], "affected_rows": 1,
    "plan": null, "spatial_context": null, "is_explain": false
  }
}
```

`result` es exactamente la serialización JSON del `QueryResponse` local mediante
`to_response`, sin campos extra. El ejemplo usa el plan nulo de una mutación.
Una lectura real adjunta el plan local que produzca el motor.

Para `SELECT nombre FROM items WHERE id >= 42 ORDER BY id ASC LIMIT 10`, el
coordinador envía a ambos fragmentos `scan` con este SQL (y un UUID distinto por
petición):

```json
{
  "protocol_version": 1,
  "catalog_version": 1,
  "request_id": "2c2d7cf8-adca-449c-b21e-e6e38b6cd1d9",
  "table": "items",
  "sql": "SELECT * FROM items WHERE id >= 42;"
}
```

La respuesta usa el mismo sobre de ejecución. Dentro de `result`, `columns`
es `["id", "nombre"]`, `column_types` es `["INT", "VARCHAR"]` y una posible
fila es `[42, "Quipu"]`; una tabla vacía conserva esas columnas y tipos con
`rows: []`. `affected_rows` es 0. El coordinador exige esquemas coincidentes,
ordena por id, proyecta nombre y toma diez filas globalmente. En la respuesta
pública distribuida usa `plan: null`, `spatial_context: null`, `is_explain: false`.
No devuelve el plan de un solo nodo como si describiera la consulta completa.

Para borrar la fila, `execute` recibe el mismo sobre con un UUID nuevo y
`sql: "DELETE FROM items WHERE id = 42;"`. Devuelve el mismo tipo de resultado
de mutación, con 0 o 1 filas afectadas.

Un SELECT de tabla `single`, incluyendo búsquedas especializadas, usa `execute`
con el SQL original. No se vuelve a ordenar ni a truncar su respuesta. Las rutas
de carga CSV/multimedia existentes se utilizan solo como proxy al propietario
`single`, después de validar identidad/versiones mediante el endpoint de catálogo;
se mantienen sus cuerpos multipart/JSON y códigos actuales, sin este sobre.
La configuración permanece estática durante el tráfico. No se usan esas rutas
para cargar tablas `range` ni se permite acceso externo directo que eluda su
validación de pertenencia.

## 5. Tipos y serialización

Los valores de `rows` son listas en el orden de `columns`, con tipos declarados
en `column_types`. No inferir tipos de filas vacías ni convertir cadenas numéricas.

| Tipo QuipuDB | Valor JSON |
|---|---|
| INT | Entero int32, nunca booleano. |
| DOUBLE | Número finito IEEE 754; no NaN ni Infinity. |
| BOOL | `true` o `false`, no 0/1. |
| VARCHAR(n) | Cadena Unicode, validada por el límite de bytes UTF-8 del motor. |
| DATE | Cadena ISO `YYYY-MM-DD`, sin hora ni zona, compatible con `datetime.date` de Python. |
| POINT | Objeto `{"latitude": -12.0464, "longitude": -77.0428}`; no invertir el orden ni usar coordenadas x/y del R-Tree. |
| VECTOR(d) | Lista de exactamente d números finitos representables en float32; conserva el redondeo del almacenamiento local. |

La representación coincide con [`engine/executor/native.py`](../engine/executor/native.py)
y la serialización JSON de Pydantic en la API. Los valores SQL de entrada siguen
la sintaxis del parser, no la notación JSON de esta tabla. No existe un endpoint
de inserción de filas JSON en v1.
POINT exige números finitos, latitud en [-90, 90] y longitud en [-180, 180],
como la conversión local existente.

El esquema físico no incluye un tipo NULL. No aceptar `null` para insertar
celdas. Las respuestas locales que produzcan valores nulos calculados los
preservan, junto con su tipo de columna; los nulos de metadatos (`size`, `plan`,
`spatial_context`, límites) tienen el significado documentado.

## 6. Errores y resultado incierto

Todos los errores internos salvo salud usan este sobre. Ejemplo: un nodo recibe
una versión de catálogo desactualizada y rechaza la petición antes de ejecutarla.

```json
{
  "protocol_version": 1,
  "catalog_version": 2,
  "request_id": "de783f4e-a813-4fa1-86ec-b7e3abc40291",
  "node_id": "n1",
  "fragment_id": "items_f0",
  "error": {
    "code": "CATALOG_VERSION_MISMATCH",
    "message": "Se recibió catálogo 1; el nodo requiere 2",
    "execution_state": "not_started",
    "kind": null,
    "line": null, "column": null, "end_line": null, "end_column": null
  }
}
```

El sobre informa las versiones del servidor. `fragment_id` es `null` en errores
de catálogo o rutas sin fragmento. `request_id` es `null` si no pudo validarse.
Las coordenadas SQL y `kind` conservan los valores del motor (`lex`, `parse`,
`semantic`, `unsupported` o `null`); apuntan al SQL recibido por el nodo.
Si el coordinador generó otro SQL para `scan`, no atribuye esas coordenadas al
SQL público: devuelve posiciones nulas y conserva el diagnóstico interno.

| HTTP | `error.code` | Significado |
|---|---|---|
| 422 | `INVALID_REQUEST` | JSON, campos o tipos inválidos. |
| 409 | `PROTOCOL_VERSION_MISMATCH` | Versión de protocolo incompatible. |
| 409 | `CATALOG_VERSION_MISMATCH` | Catálogo diferente; no reintentar con otra versión automáticamente. |
| 409 | `SCHEMA_MISMATCH` | El esquema/índices físicos no corresponden a la configuración. |
| 404 | `FRAGMENT_NOT_FOUND` | El fragmento no existe en el catálogo. |
| 409 | `WRONG_FRAGMENT` | Propietario, tabla o clave no corresponden al destino. |
| 400 | `UNSUPPORTED_DISTRIBUTED` | Operación fuera de la matriz v1. |
| 400 | `SQL_ERROR` | Error SQL del procesador; conservar mensaje y ubicación cuando corresponda. |
| 409 | `DUPLICATE_KEY` | Duplicado identificado por el motor, sin inferirlo por texto del mensaje. |
| 503 | `NODE_NOT_READY` | Nodo iniciado pero no habilitado. |
| 500 | `EXECUTION_ERROR` | Fallo durante ejecución, E/S o mantenimiento de índices. |

`execution_state` tiene dos valores: `not_started` (validación falló antes de
invocar el motor) y `unknown` (se invocó el motor y no puede afirmarse que no
hubo efectos). No equivale a rollback confirmado. Si el adaptador no puede
clasificar un duplicado porque el procesador lo convirtió en error semántico,
devuelve `SQL_ERROR`; no analiza cadenas para fabricar `DUPLICATE_KEY`.

El cliente genera `NODE_UNAVAILABLE` para fallo de conexión y
`NODE_TIMEOUT` para timeout. No son respuestas inventadas del nodo. Si una
escritura pudo haberse enviado, el estado es `unknown`; ante certeza de que no
se envió, `not_started`. Una respuesta inválida, identidad o fragmento distinto,
tipos de resultado incompatibles o una desconexión tras escribir el cuerpo se
trata como `INVALID_NODE_RESPONSE` y nunca como éxito.

El coordinador traduce estos errores al `QueryErrorResponse` público existente:
`error` comienza con `[CODIGO]` y explica nodo/fragmento y, si corresponde,
«resultado de escritura incierto; no se reintentó». Mantiene `kind` y posiciones
válidas. No añade campos internos a la API pública. Usa 503 para indisponibilidad,
504 para timeout y 502 para respuesta inválida; conserva los códigos HTTP de
errores válidos del nodo. El cliente actual muestra el texto de error recibido.

No hay reintentos automáticos, ni siquiera de lecturas en v1. Repetir INSERT
tras un timeout podría encontrar un duplicado; eso no prueba por sí solo qué
petición insertó la fila. Una lectura multinodo falla por completo si falla
algún destino requerido. Nunca devuelve filas parciales con HTTP 200.

## 7. Acuerdos de integración

1. Mauricio implementa los modelos de esta forma JSON en #276 y los endpoints
   en #277. Oswaldo consume esos modelos, sin mantener una segunda definición
   incompatible; puede empezar con fixtures JSON mientras se implementan.
2. El cliente verifica `node_id`, `fragment_id`, UUID y versiones de toda
   respuesta. El router usa las funciones de pertenencia de #276.
3. El nodo reutiliza el motor bajo exclusión mutua y descarga índices mediante
   `flush()` al cerrar. No ofrece una promesa adicional de durabilidad frente
   a caída del proceso o del sistema operativo.
4. #280 prueba los ejemplos, esquemas vacíos, tipos POINT/DATE/VECTOR, errores,
   fronteras, rechazo sin mutación, proxy multimodal y fallos de transporte.
5. Cualquier cambio incompatible de campos, tipos, rutas o semántica requiere
   revisar este contrato y subir la versión de protocolo antes de integrar.
   Modificar solo datos de topología exige subir `catalog_version`.
