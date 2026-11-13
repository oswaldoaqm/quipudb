# Coordinador y comunicación entre nodos — Issue #278

Implementa el lado del coordinador del [contrato v1](contrato-distribuido-v1.md):
el cliente HTTP que habla con cada nodo y el proceso coordinador que los revisa
y arma la vista lógica de las tablas. `POST /query`, que usa estos clientes, se
describe en [Enrutamiento y combinación de resultados](enrutamiento-distribuido.md) (#279).

| Archivo | Qué hace |
|---|---|
| [`client.py`](../engine/distributed/client.py) | `NodeClient`: un cliente httpx por nodo, sobre v1, validación de respuestas y traducción de fallos. |
| [`coordinator.py`](../engine/distributed/coordinator.py) | `Coordinator`, la API pública (`GET /health`, `GET /tables`) y la línea de comandos. |
| [`coordinator_test_support.py`](../engine/distributed/coordinator_test_support.py) | Nodos falsos con `httpx.MockTransport` para probar fallos sin motor ni red. |

## Cliente de nodo

Hay un `NodeClient` por cada nodo del catálogo. Expone las cuatro rutas
internas: `health()`, `describe()` (catálogo del nodo), `execute()` y `scan()`.
Cada llamada arma el sobre con `protocol_version`, `catalog_version` y un
`request_id` nuevo, y usa los timeouts del contrato: 3 s para conectar y para
obtener una conexión del pool, 30 s para leer o escribir. No reintenta ni sigue
redirecciones.

Una respuesta solo se acepta si es del nodo esperado, trae las versiones del
coordinador, el mismo `request_id` y, en las rutas de fragmento, el mismo
`fragment_id`. Cualquier otra cosa es un fallo, nunca un éxito.

Todos los fallos llegan como `NodeFailure`, con el código del contrato:

| Situación | Código | HTTP público | `execution_state` |
|---|---|---|---|
| El nodo rechazó la petición (`SQL_ERROR`, `WRONG_FRAGMENT`, …) | el del nodo | el del nodo | el del nodo |
| No se pudo conectar | `NODE_UNAVAILABLE` | 503 | `not_started` |
| No hubo conexión libre en el pool a tiempo | `NODE_TIMEOUT` | 504 | `not_started` |
| Se envió y no respondió a tiempo | `NODE_TIMEOUT` | 504 | `unknown` |
| La conexión se cortó después de enviar | `INVALID_NODE_RESPONSE` | 502 | `unknown` |
| Respuesta ilegible, de otro nodo, otro fragmento u otra versión | `INVALID_NODE_RESPONSE` | 502 | `unknown` si era escritura |

Los errores del nodo conservan además `kind` y la ubicación SQL. El texto
público tiene la forma `[CODIGO] nodo n1, fragmento items_f0: mensaje`, y en una
escritura con estado `unknown` agrega "resultado de escritura incierto; no se
reintentó".

## Coordinador

`Coordinator(catalog)` crea los clientes y no abre ningún archivo de datos.

`check_nodes()` revisa todos los nodos en paralelo. Un nodo queda listo si
responde, dice estar listo, aloja exactamente los fragmentos que el catálogo le
asigna y cada fragmento tiene el esquema, la organización y los índices
declarados. Un nodo caído o distinto no es una excepción: queda como no listo,
con el motivo.

`tables()` pregunta el catálogo a los nodos que alojan fragmentos y arma una
entrada por tabla lógica. En una tabla `range` suma el `record_count` de sus
fragmentos; en una `single` devuelve la descripción de su dueño. Si un nodo
necesario no responde o describe otro esquema, devuelve error: nunca un conteo
incompleto.

## API pública

| Ruta | Respuesta |
|---|---|
| `GET /health` | 200 si todos los nodos están listos, 503 si no; el estado y el motivo de cada nodo. |
| `GET /tables` | La misma forma de `GET /tables` de la API local, con las tablas lógicas. |
| `POST /query` | La misma forma de la API local; ver [enrutamiento](enrutamiento-distribuido.md). |

Los errores usan el `QueryErrorResponse` de siempre, así que el frontend los
muestra sin cambios. El coordinador escucha en el puerto 8000 y acepta los
mismos orígenes que la API local.

## Levantar dos nodos y el coordinador

Con los bindings compilados y un catálogo cuyos nodos apunten a 8001 y 8002,
como [`catalogo-nodos-locales-v1.json`](ejemplos/catalogo-nodos-locales-v1.json).
Primero se preparan los directorios de los nodos, una sola vez, como indica
[Operaciones sobre fragmentos](operaciones-fragmentos.md). Después, cada comando
en su terminal:

```bash
python -m engine.distributed.node_server --catalog docs/ejemplos/catalogo-nodos-locales-v1.json \
    --catalog-version 1 --node-id n1 --data-dir /tmp/quipudb-dist/n1 --port 8001
python -m engine.distributed.node_server --catalog docs/ejemplos/catalogo-nodos-locales-v1.json \
    --catalog-version 1 --node-id n2 --data-dir /tmp/quipudb-dist/n2 --port 8002
python -m engine.distributed.coordinator --catalog docs/ejemplos/catalogo-nodos-locales-v1.json \
    --catalog-version 1
```

```bash
curl http://localhost:8000/health
curl http://localhost:8000/tables
```

El coordinador puede arrancar antes que los nodos: al arrancar informa en el
log cuáles no están listos, y `GET /health` vuelve a revisarlos en cada llamada.

## Pruebas

- [`test_client.py`](../engine/distributed/test_client.py): forma de la
  petición, errores del nodo, fallos de transporte y respuestas inválidas con
  nodos falsos; ciclo completo y errores reales contra el servidor del #277 en
  memoria.
- [`test_coordinator.py`](../engine/distributed/test_coordinator.py): revisión
  de nodos (caído, no listo, otros fragmentos, otro esquema), conteos de
  `/tables` para `range` y `single`, la API pública y un recorrido con dos
  nodos reales.
- [`test_coordinator_procesos.py`](../engine/distributed/test_coordinator_procesos.py):
  dos nodos como procesos Uvicorn por TCP; se cae uno y el coordinador lo
  informa sin romperse.

Las pruebas con nodos falsos no necesitan los bindings, así que también corren
en el job de Python sin el motor.
