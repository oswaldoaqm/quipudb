# Enrutamiento y combinación de resultados — Issue #279

El coordinador del #278 ya sabía hablar con cada nodo. Este issue le agrega
`POST /query`: decide qué fragmentos atienden una sentencia, les manda el SQL
que corresponde y junta lo que responden en una sola respuesta con la forma de
la API local. El frontend no cambia.

| Archivo | Qué hace |
|---|---|
| [`router.py`](../engine/distributed/router.py) | Analiza la sentencia con el parser y el binder del motor y decide el destino. No usa red ni disco. |
| [`merge.py`](../engine/distributed/merge.py) | Valida las filas de cada fragmento y las combina: orden, proyección y LIMIT. |
| [`coordinator.py`](../engine/distributed/coordinator.py) | `Coordinator.query()` y la ruta `POST /query`. |

## Del SQL al destino

El router parsea la consulta y busca la tabla en el catálogo. Los errores
léxicos, sintácticos y semánticos salen con el mismo texto y la misma posición
que en la API local, porque son los del motor. Lo que v1 no admite sale como
error `unsupported` con el prefijo `[UNSUPPORTED_DISTRIBUTED]` y la posición de
la parte no admitida. En ningún caso se contacta a un nodo.

Hay dos destinos:

| Sentencia | Destino | SQL que recibe el nodo |
|---|---|---|
| Cualquiera sobre una tabla `single` (SELECT, EXPLAIN, INSERT, DELETE) | `execute` en el dueño | El original. La respuesta vuelve sin tocar, con su plan. |
| INSERT en una tabla `range` | `execute` en el fragmento dueño de la clave | El original. |
| DELETE en una `range` con `WHERE pk = valor` | `execute` en el fragmento dueño de esa clave | El original. |
| SELECT en una `range` | `scan` en cada fragmento que puede tener filas | `SELECT * FROM tabla [WHERE ...];` generado desde el AST. |

El dueño de una clave lo da `owner_for_key` del #276. Para un SELECT con WHERE
sobre la clave primaria, `fragments_for_comparison` descarta los rangos que no
pueden tener filas. Con el catálogo de ejemplo (`items_f0` hasta 9999 en n1,
`items_f1` desde 10000 en n2):

| WHERE | Fragmentos |
|---|---|
| `id = 9999` | `items_f0` |
| `id = 10000` | `items_f1` |
| `id < 10000` | `items_f0` |
| `id <= 10000` | `items_f0`, `items_f1` |
| `id > 2147483647` | ninguno |
| `nombre = 'x'` o sin WHERE | `items_f0`, `items_f1` |

Si no queda ningún fragmento, la respuesta es una tabla vacía con las columnas
y los tipos pedidos, sin preguntar a ningún nodo.

### SELECT que admite una tabla range

Columnas o `*` (también calificadas como `items.id` y repetidas), un WHERE
opcional de la forma `columna op literal` con `=`, `<`, `<=`, `>` o `>=` sobre
INT, DOUBLE, VARCHAR, BOOL o DATE, `ORDER BY` por la clave primaria en
cualquier dirección y `LIMIT`. Queda fuera: GROUP BY y agregados, SCORE y
MATCH, SIMILAR_TO, predicados espaciales, BETWEEN, ORDER BY por otra columna o
por distancia, JOIN, EXPLAIN, DDL, BEGIN/END y lotes de varias sentencias.

### El SQL del scan

El SELECT que recibe cada nodo se escribe desde el AST, no copiando texto de
la consulta. Cada literal se vuelve a escribir en la sintaxis del lexer: las
cadenas duplican la comilla simple (`'o''brien'`), las fechas van como
`DATE 'AAAA-MM-DD'` y los DOUBLE se escriben sin notación exponencial, que el
lexer no acepta. Para eso se toma el decimal más corto que representa el mismo
float (`repr`) y se escribe con `Decimal` en notación fija: `1e-20` se envía
como `0.00000000000000000001`, y el nodo lee exactamente el mismo valor.

## Combinación

Los scans van en paralelo. Si uno falla, falla la consulta completa con el
código del contrato (503, 504 o 502 para fallos de transporte, el del nodo si
el nodo rechazó la petición). Nunca se devuelven filas parciales con 200.

Antes de usar las filas de un fragmento se comprueba que el nodo respondió lo
prometido: las columnas y los tipos de la tabla en orden físico, filas del
ancho correcto, que no sea un EXPLAIN y que cada clave sea un entero dentro
del rango del fragmento y no se repita. Si algo falla la consulta termina con
`INVALID_NODE_RESPONSE` (502), con el nodo y el fragmento en el mensaje.

No hace falta eliminar duplicados. Los rangos de los fragmentos no se
superponen y cada fragmento se consulta una sola vez, así que cada fila llega
por un único camino. Por eso no se compara fila contra fila: dos filas con el
mismo `nombre` son datos legítimos y se conservan, y una clave repetida solo
puede ser un error del nodo, que se rechaza en vez de ocultarse.

Después se concatenan las filas en el orden de los rangos, se ordenan por la
clave si hubo ORDER BY, se proyectan las columnas pedidas y se aplica el LIMIT
sobre el total. La respuesta lleva `plan: null`, `spatial_context: null` y
`is_explain: false`: el plan de un nodo no describe la consulta completa.

Para `SELECT nombre FROM items WHERE id >= 42 ORDER BY id DESC LIMIT 2`:

```text
n1 / items_f0  ←  SELECT * FROM items WHERE id >= 42;   →  [42, 'a'], [9999, 'b']
n2 / items_f1  ←  SELECT * FROM items WHERE id >= 42;   →  [10000, 'c']
combinado: ordenar por id DESC → [10000, 9999, 42]; proyectar nombre; LIMIT 2
respuesta: [['c'], ['b']]
```

Sin ORDER BY las filas salen en el orden de los rangos y, dentro de cada uno,
en el orden en que las devolvió el nodo. Igual que en la API local, ese orden
no está garantizado.

## Errores

Todos usan el `QueryErrorResponse` de la API local.

| Origen | HTTP | `kind` y posición |
|---|---|---|
| Parser o binder en el coordinador | 400 | Los del motor, sobre la consulta del usuario. |
| Fuera de la matriz v1 | 400 | `unsupported`, sobre la parte no admitida. |
| Error del nodo en un `execute` | El del nodo | Los del nodo: recibió el SQL original. |
| Error del nodo en un `scan` | El del nodo | Se conserva `kind`, las posiciones van en `null`: el nodo leyó el SQL generado, no el del usuario. |
| Nodo caído, lento o con respuesta inválida | 503, 504, 502 | `null`. |

Una escritura cuyo resultado no se puede asegurar (timeout después de enviar,
o un error del nodo con `execution_state: unknown`) agrega "resultado de
escritura incierto; no se reintentó". El coordinador no reintenta nada.

## Fuera de alcance

El contrato prevé que las cargas CSV y multimedia de una tabla `single` pasen
por el coordinador hacia su dueño. El servidor de nodo v1 solo expone las
cuatro rutas internas, así que el coordinador no tiene a dónde reenviarlas; las
cargas siguen haciéndose en cada nodo, antes de levantarlo.

## Probarlo

Con los dos nodos y el coordinador levantados como indica
[Coordinador y comunicación entre nodos](coordinador-distribuido.md):

```bash
q() { curl -s localhost:8000/query -H 'Content-Type: application/json' -d "{\"sql\":\"$1\"}"; echo; }
q "INSERT INTO items VALUES (9999, 'frontera')"
q "INSERT INTO items VALUES (10000, 'quipu')"
q "SELECT nombre, id FROM items ORDER BY id DESC LIMIT 2"
q "SELECT * FROM items WHERE id = 10000"
q "SELECT * FROM items ORDER BY nombre"
q "DELETE FROM items WHERE id = 9999"
```

El primer SELECT devuelve `[["quipu", 10000], ["frontera", 9999]]` combinando
los dos nodos; el segundo solo consulta n2; el tercero responde 400
`[UNSUPPORTED_DISTRIBUTED] en una tabla range solo se puede ordenar por id`.
Si se detiene n2, `SELECT * FROM items` responde 503 `[NODE_UNAVAILABLE]` y
`SELECT * FROM items WHERE id < 10000` sigue funcionando, porque solo necesita
a n1.

## Pruebas

- [`test_router.py`](../engine/distributed/test_router.py): destino de cada
  sentencia, fragmentos según el WHERE (fronteras 9999/10000 y extremos INT),
  SQL generado y su reparseo, DOUBLE sin exponente, tablas `single` y cada
  caso fuera de la matriz con su posición.
- [`test_merge.py`](../engine/distributed/test_merge.py): orden y LIMIT
  globales, valores repetidos legítimos, respuestas vacías y cada forma de
  respuesta inválida.
- [`test_coordinator_query.py`](../engine/distributed/test_coordinator_query.py):
  `POST /query` con nodos falsos (qué nodo recibe qué, nodo caído, timeout de
  escritura, errores del scan sin posiciones) y con dos nodos reales en
  memoria, comparando 18 consultas contra el motor local con los mismos datos.
- [`test_coordinator_procesos.py`](../engine/distributed/test_coordinator_procesos.py):
  el mismo recorrido con dos procesos Uvicorn por TCP, incluida la caída de un
  nodo.
