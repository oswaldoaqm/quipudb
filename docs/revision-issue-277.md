# Evidencia y revisión — Issue #277

Fecha: 2026-11-12. Implementación local de los criterios proporcionados por el
usuario y del contrato #275. El cuerpo remoto del issue no pudo recuperarse;
no se afirman criterios adicionales de GitHub.

## Base y alcance

- Rama conservada: `feat/distributed-fragmentation`.
- HEAD inicial y final: `fb05e20b86d96160c4b7c525024df6a7da0bd4e4`.
- Base común con la referencia local `origin/main`:
  `5a14a986fa1669adb1b7bcae66c1072e043fc42c` (esa referencia era también el tip
  local de origin/main). No se ejecutó fetch ni se supone que sea el tip remoto actual.
- #276 disponible en 13 commits sobre esa base: modelos, catálogo, rangos,
  preparación real, pruebas y documentación presentes. No se implementó sobre mocks.
- Al inicio no había modificaciones rastreadas; solo `.Rhistory` no rastreado.
- No se ejecutaron add, commit, push, rebase, switch, checkout ni reset.
- SHA-1 de `.Rhistory` antes y después:
  `da39a3ee5e6b4b0d3255bfef95601890afd80709`. No se editó ni incluyó en los cambios.

## Matriz de criterios de aceptación

| # | Criterio | Implementación/evidencia | Estado |
|---|---|---|---|
| 1 | Revisar y reutilizar almacenamiento existente | Servicio utiliza Database, QueryProcessor, binder, DML e índices; no modifica C++ ni bindings. | Cumplido |
| 2 | Inserción que respeta fragmentación | `admission.validate_sql` + `owner_for_key`; `test_node_insert.py`. | Verificado con core real |
| 3 | Validar PK, fragmento y nodo | `resolve_fragment` y pertenencia; INT32 mínimo, máximo y frontera 9999/10000. | Verificado |
| 4 | Búsqueda local | `scan`, comparaciones escalares, respuestas vacías tipadas; `test_node_scan.py`. | Verificado con core real |
| 5 | Eliminación del fragmento correcto | DELETE range por igualdad de PK; repetición devuelve cero; `test_node_delete.py`. | Verificado con core real |
| 6 | No afectar otros fragmentos | Lecturas independientes, rechazo de operaciones ajenas y hashes de archivos del otro nodo. | Verificado entre procesos |
| 7 | Mantener índices y validaciones existentes | B+ y hash comprobados directamente tras insertar, duplicar y borrar; single usa R-Tree, FULLTEXT, HNSW e IVF. | Verificado |
| 8 | Servicio y endpoints internos v1 | Health, catalog, execute y scan en FastAPI; pruebas HTTP TestClient y Uvicorn real. | Implementado/verificado |
| 9 | Identidad, versiones, esquemas y peticiones | Validación estricta antes de ejecutar; auditoría en arranque y de metadatos por petición; rechazos sin mutación. | Verificado |
| 10 | Respuestas/errores del protocolo | Modelos #276, estados not_started/unknown, posiciones SQL, UUID, códigos HTTP y DuplicateKey por causa tipada. | Verificado |
| 11 | Pruebas de operaciones | Unitarias de admisión/errores/identidad y pruebas nativas por operación. | Verificado |
| 12 | Al menos dos nodos independientes | Dos procesos Uvicorn con puertos y directorios distintos; no simulación de motor. | Verificado |
| 13 | Persistencia, aislamiento e índices | Reinicio de ambos procesos, control de archivos distintos, auditoría y reapertura HNSW/IVF. | Verificado |
| 14 | Documentación y resultados reales | Guía, JSON ejecutables, esta evidencia y plan manual de commits. | Entregado |

## Resultados realmente ejecutados

| Comando/comprobación | Resultado |
|---|---|
| Suite final `pytest engine/distributed` con bindings y permiso de puertos | **283 aprobadas**, ninguna omitida; 9.14 s. Incluye las 155 pruebas #276 y **128 pruebas nuevas**. |
| Regresión `pytest engine benchmarks` con bindings, puertos y ps permitidos | **2288 aprobadas, 2 fallidas, 5 omitidas**, 125.08 s. |
| `ctest --test-dir build-py --output-on-failure --no-tests=error` | **694/694 aprobadas**, 61.85 s. No se modificó ni recompiló el núcleo para este issue. |
| `ruff check engine benchmarks` | Aprobado. |
| `git diff --check` | Aprobado. |
| Exportación limpia de HEAD: pruebas multimedia afectadas | **15 aprobadas y las mismas 2 fallidas**. No incluye el código nuevo #277. |

La regresión amplia se ejecutó antes de añadir dos pruebas adicionales de
documentación e IVF, sin cambios posteriores al código de producción. Ambas
están incluidas en las 283 pruebas de la suite distribuida final. No se suma
ese resultado para inventar una ejecución global distinta.

La primera ejecución de las pruebas TCP bajo sandbox produjo dos errores
`PermissionError` al abrir sockets. Se repitieron con autorización fuera del
sandbox y pasaron; no se cambiaron por mocks ni se omitieron. Las pruebas
globales se ejecutaron con permiso de sockets y `ps` desde el principio.

Se observó la advertencia existente de deprecación Starlette/httpx en TestClient.
Las cinco omisiones globales corresponden al entorno RAG de Quipucamayoc,
`quora.zip`, dos conjuntos de CSV oficiales no versionados y PostgreSQL de prueba
(`QUIPUDB_DSN_GIN`). Ninguna prueba nueva #277 fue omitida.

## Dos fallos multimedia preexistentes

En `engine/multimedia/test_features_extremo_a_extremo.py`:

1. `test_el_vector_de_una_imagen_describe_algo`: el vector extraído es cero.
2. `test_la_consulta_pasa_por_el_mismo_pipeline_que_el_dataset`: no se identifica
   la variante de imagen esperada mediante similitud.

Se exportó `git archive HEAD engine pyproject.toml` a un directorio temporal y
se ejecutó allí ese archivo con el mismo intérprete/dependencias. Se reprodujeron
ambos errores, sin incluir/importar los nuevos módulos. No se atribuyen al #277,
ni se cambió el pipeline para silenciarlos. La suite global **no está toda verde**;
su corrección queda fuera de este issue. No se afirma la causa precisa de los
fallos solo a partir de esta reproducción.

## Evidencia de motores y HTTP reales

`test_operaciones_http_aislamiento_y_persistencia` inicia dos procesos distintos
usando la CLI de producción, TCP loopback y bindings C++. Prepara los archivos
mediante #276 y envía INSERT/scan/DELETE al HTTP del nodo correspondiente.

| Momento | n1 / items_f0 | n2 / items_f1 |
|---|---|---|
| Después de insertar | -2147483648, 9999 | 10000, 2147483647 |
| Tras rechazos de insertar/borrar 10000 en n1 | Sin cambios | Sin cambios |
| Después de borrar 9999 en n1 | -2147483648 | 10000, 2147483647 |
| Tras detener y arrancar ambos procesos | -2147483648 | 10000, 2147483647 |

La prueba compara hashes de los archivos de n2 antes/después de las operaciones
en n1, comprueba que los archivos de ambos nodos no son el mismo archivo físico,
verifica conteos offline con los procesos detenidos y exige PID nuevos al reiniciar.
`test_dos_servidores_no_comparten_un_directorio` inicia otro servidor sobre n1:
el segundo responde not_ready sin impedir que el original continúe disponible.

Las pruebas single comparan respuestas con el motor local para texto, radio,
polígono, k-NN, HNSW, IVF, GROUP BY y EXPLAIN. Incluyen fecha/POINT/VECTOR en JSON
y extracción MFCC real desde archivos de audio y vocabulario local, sin simular
la función de extracción.

## Inventario de cambios

Archivos nuevos de producción en `engine/distributed/`:
`node_errors.py`, `node_requests.py`, `admission.py`, `node_lease.py`,
`fragments.py`, `node_api.py`, `node_server.py`.

Utilidades nuevas de pruebas: `node_test_support.py`, `node_process_support.py`.

Pruebas nuevas: `test_node_requests.py`, `test_node_errors.py`, `test_node_lease.py`,
`test_admission_insert.py`, `test_admission_scan.py`, `test_admission_delete.py`,
`test_node_insert.py`, `test_node_scan.py`, `test_node_delete.py`,
`test_node_indexes.py`, `test_node_lifecycle.py`, `test_node_api.py`,
`test_node_failures.py`, `test_node_single.py`, `test_node_processes.py`,
`test_node_examples.py`.

Documentos nuevos: `docs/operaciones-fragmentos.md`, `docs/revision-issue-277.md`,
`docs/commits-issue-277.md`. Ejemplos nuevos: `docs/ejemplos/catalogo-nodos-locales-v1.json`
y `nodo-insert-v1.json`, `nodo-scan-v1.json`, `nodo-delete-v1.json` en ese directorio.

Archivos existentes modificados:

- `engine/distributed/storage.py`: auditoría reutilizable sobre el handle activo.
- `engine/distributed/conftest.py`: fixtures adicionales, manteniendo el fixture #276.
- `.github/workflows/ci.yml`: Uvicorn y ejecución explícita de `engine/distributed`
  en el job con bindings. La entrada de la suite no estaba en la base recibida.
- `docs/arquitectura-distribuida.md`, `docs/contrato-distribuido-v1.md`: estado y
  enlace al servicio ya implementado, sin alterar formatos ni restricciones v1.

## Límites y entrega

Los criterios locales del #277 tienen implementación y evidencia. No se probaron
despliegues Windows ni recuperación ante cortes de energía; el alcance no promete
esas garantías. Las herramientas offline y la API local antigua no deben abrir
archivos de un nodo activo, porque no participan del lease nuevo.

No se implementaron APIs de carga, coordinación, cliente HTTP de producción,
enrutamiento global, combinaciones, replicación ni transacciones distribuidas.
Oswaldo consume los modelos existentes y las cuatro rutas documentadas en
[Operaciones sobre fragmentos](operaciones-fragmentos.md). Los errores de escritura
incierta no deben provocar reintentos automáticos.

La implementación está preparada para revisión con las observaciones de regresión
anteriores. No se creó PR ni se cerró el issue. El registro manual está organizado
en [29 commits significativos](commits-issue-277.md), sin ejecutar ninguno.
