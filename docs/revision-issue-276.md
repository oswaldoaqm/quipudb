# Revisión y commits manuales — Issue #276

Fecha: 2026-11-12. Rama de trabajo: `feat/distributed-fragmentation`. La implementación y los comandos reproducibles están en [Fragmentación horizontal](fragmentacion-horizontal.md).

## Matriz de criterios y evidencia

La matriz corresponde a los criterios proporcionados para #276 y al contrato fusionado del #275. No supone requisitos adicionales de otros issues.

| Criterio | Estado | Evidencia |
|---|---|---|
| Revisar y reutilizar almacenamiento, catálogo, SQL, índices y bindings existentes | Implementado | `storage.py` usa `Database`, `to_native_schema`, `describe_catalog`; demo con `QueryProcessor`. Núcleo sin cambios. |
| PK INT32, inferior inclusivo, superior exclusivo, null como infinito | Verificado | `contracts.py`, `partitioning.py`; pruebas de mínimos/máximos y fronteras. |
| Rangos configurables y propietario único determinista | Verificado | `owner_for_key`; demo automatizada con fronteras 10000 y -5. |
| Identificar fragmento y nodo, seleccionar por comparación | Verificado | `Fragment.node_id`, `catalog.node`, `fragments_for_comparison`; oráculo de las cinco comparaciones. |
| Catálogo JSON persistente con nodos, tablas, columnas, índices y versiones | Verificado | `load_catalog`, ejemplo normativo y ejemplo reproducible; recarga sin cambiar bytes. |
| Rechazar huecos, solapamientos, vacíos, límites inválidos y cobertura incompleta | Verificado | `test_catalog.py`, `test_contracts.py`, incluyendo intervalos vacíos dentro de INT32. |
| Rechazar duplicados, nodos inexistentes, claves/esquemas/tipos/versiones inválidos | Verificado | Validaciones locales/globales y pruebas parametrizadas; `expected_catalog_version`. |
| Configuración estricta y compatible con #275 | Verificado | Ocho ejemplos normativos validados en `test_protocol.py`; campos adicionales, faltantes y tipos incorrectos rechazados. |
| Dos motores, directorios, catálogos y archivos de índices independientes | Verificado con motor real | `test_storage.py` y demo: procesos separados y comprobación de `samefile`. |
| Registros presentes solo en su propietario | Verificado con motor real | Inserción local con QP, consulta de todas las claves en ambos nodos y auditoría de PK. |
| Persistencia al cerrar y reabrir | Verificado con motor real | Nuevos procesos en fase verify y tercera apertura con `--verify-only`. |
| Detectar filas mal ubicadas y metadatos físicos distintos | Verificado | Pruebas que introducen una fila ajena y retiran un índice antes de auditar. |
| Pruebas nuevas y regresión pertinente ejecutadas | Verificado con observaciones | Resultados reales y fallos preexistentes descritos debajo. |
| Mantener funcionalidades existentes | Sin regresión nueva identificada | Sin cambios al motor; 694 pruebas C++ aprobadas. Dos fallos multimedia reproducidos en HEAD limpio. |
| Documentación de uso, configuración, pruebas y límites | Entregado | `fragmentacion-horizontal.md`, este documento y JSON de ejemplo. |
| Respetar límites #277–#280 | Cumplido | No hay endpoints, cliente HTTP, router SQL ni coordinador. No se modificó el workflow de GitHub Actions. |
| Git y `.Rhistory` | Cumplido | Se realizaron 13 commits manuales. El archivo `.Rhistory` no fue incluido ni modificado. |
| Plan de commits pequeños conforme al repositorio | Entregado | Tabla al final; mensajes cotejados con el validador real. |

## Resultados de validación

Entorno local: Python 3.14.6, Pydantic 2.13.4 y bindings C++ recompilados en `build-py`. No se cambió el entorno de dependencias ni el código multimedia.

| Comprobación | Resultado observado |
|---|---|
| `.venv/bin/cmake --build build-py --parallel 4` | Compilación satisfactoria del núcleo, pruebas y bindings actuales. |
| `pytest engine/distributed` con bindings recompilados | **155 aprobadas**, ninguna omitida. |
| `ctest --test-dir build-py --output-on-failure --no-tests=error` | **694/694 aprobadas**. |
| `ruff check engine benchmarks` | Aprobado. |
| `ruff check engine/distributed scripts/demo_fragmentacion.py` | Aprobado. |
| `git diff --check` | Aprobado. |
| Regresión `pytest engine` después de recompilar | **1784 aprobadas, 2 fallidas**: los dos casos multimedia detallados debajo. |
| Regresión final `pytest engine benchmarks`, bindings actuales y permiso para `ps` | **2162 aprobadas, 2 fallidas, 5 omitidas**, en 116.21 s. Solo fallan los dos casos multimedia preexistentes. |
| Benchmarks vectoriales y comparación de métricas, repetidos fuera del sandbox | **11 aprobadas**; necesitan `ps` para medir memoria. |
| Copia limpia de HEAD: `test_features_extremo_a_extremo.py` | **15 aprobadas, las mismas 2 fallidas**, sin importar ni incluir los archivos de #276. |

La primera regresión amplia comenzó con bindings previamente compilados y bajo sandbox: obtuvo 2158 aprobadas, 6 fallidas y 5 omitidas. Un fallo era un binding desactualizado sin `create_text_index` y quedó resuelto al recompilar; tres fallos eran benchmarks bloqueados por `ps` y pasaron con permiso fuera del sandbox. No se alteraron los tests para omitir esos errores.

Las cinco omisiones de la ejecución final corresponden al entorno RAG de Quipucamayoc, `quora.zip`, dos juegos de CSV oficiales no versionados y una conexión PostgreSQL de prueba (`QUIPUDB_DSN_GIN`). Ninguna prueba nueva del #276 se omitió. Se observó además una advertencia de deprecación del TestClient Starlette/httpx existente; no es un fallo de prueba.

Los dos fallos multimedia restantes son:

- `engine/multimedia/test_features_extremo_a_extremo.py::test_el_vector_de_una_imagen_describe_algo`: el vector extraído resulta cero.
- `engine/multimedia/test_features_extremo_a_extremo.py::test_la_consulta_pasa_por_el_mismo_pipeline_que_el_dataset`: la similitud de la consulta no identifica la variante esperada.

Se reprodujeron exportando `HEAD` mediante `git archive` a un directorio temporal, sin cambiar la rama ni retirar los cambios del workspace, y ejecutando allí el archivo de pruebas con el mismo intérprete y dependencias. Esto demuestra que no los introduce #276 en este entorno; no determina por sí solo la causa del pipeline. Corregirlos pertenece a un trabajo multimedia separado. No se declara que la suite global esté completamente verde.

## Evidencia de almacenamiento independiente

En una ejecución real de la demo:

| Nodo | PID de carga | PID de reapertura | Claves |
|---|---|---|---|
| n1 | 37724 | 37726 | -2147483648, 9999 |
| n2 | 37725 | 37727 | 10000, 10001, 2147483647 |

Los catálogos se crearon bajo `/private/tmp/quipudb-276-demo-01/n1/catalogo.txt` y `/private/tmp/quipudb-276-demo-01/n2/catalogo.txt`. Cada directorio tiene su `items.heap`, `items.por_id.bplus_unclustered` y `items.por_nombre.extendible_hash`. Los conteos tras reapertura fueron 2 y 3. Las pruebas automatizadas repiten el experimento en directorios temporales propios y verifican que los archivos no son el mismo archivo físico. Los PID anteriores son evidencia de una ejecución, no identificadores configurables.

## Plan de commits manuales

Se realizaron 13 commits manuales, organizados por funcionalidades, pruebas y documentación. La siguiente tabla recoge los 13 commits realizados por archivos completos; **no fue necesario utilizar `git add -p`**. El historial conserva la implementación, las pruebas y la documentación. Los commits de pruebas se apoyan en los commits de código indicados. No se incluyeron `.Rhistory`, archivos generados de build ni directorios de la demo.

El script `.github/scripts/check-commits.sh` acepta tipos `feat|fix|docs|refactor|test|chore`; `distributed` no es un alcance permitido. Se utilizan `catalog`, `api`, `storage` y `docs`. Los mensajes están en minúsculas, sin punto final, en imperativo y dentro de 72 caracteres.

Las rutas de la tabla son relativas a la raíz del repositorio.

| Orden | Archivos completos | Mensaje | Dependencias |
|---|---|---|---|
| 1 | `engine/distributed/__init__.py`, `engine/distributed/contracts.py` | `feat(catalog): definir modelos inmutables de fragmentacion` | Motor/parser existentes. |
| 2 | `engine/distributed/catalog.py` | `feat(catalog): cargar y validar catalogos distribuidos` | 1. |
| 3 | `engine/distributed/partitioning.py` | `feat(catalog): resolver pertenencia e interseccion de rangos` | 1. |
| 4 | `engine/distributed/protocol.py` | `feat(api): definir sobres del protocolo distribuido v1` | 1, API schemas existente. |
| 5 | `docs/ejemplos/catalogo-distribuido-v1.json` | `docs(catalog): agregar configuracion reproducible de dos nodos` | 1–2 para validarlo. |
| 6 | `engine/distributed/conftest.py`, `engine/distributed/test_contracts.py` | `test(catalog): validar modelos y metadatos distribuidos` | 1–2, 5. |
| 7 | `engine/distributed/test_catalog.py` | `test(catalog): verificar carga y cobertura de catalogos` | 2, 6. |
| 8 | `engine/distributed/test_partitioning.py` | `test(catalog): cubrir fronteras int32 y seleccion de fragmentos` | 2–3, 6. |
| 9 | `engine/distributed/test_protocol.py` | `test(api): verificar ejemplos del contrato distribuido` | 2, 4 y documentación #275. |
| 10 | `engine/distributed/storage.py` | `feat(storage): preparar y auditar nodos independientes` | 1–3 y bindings existentes. |
| 11 | `scripts/demo_fragmentacion.py` | `feat(storage): agregar demostracion multiproceso persistente` | 2–3, 5, 10. |
| 12 | `engine/distributed/test_storage.py` | `test(storage): comprobar aislamiento y persistencia real` | 6, 10–11. |
| 13 | `docs/fragmentacion-horizontal.md`, `docs/revision-issue-276.md`, enlace en `docs/arquitectura-distribuida.md` | `docs(docs): documentar fragmentacion y evidencia del issue 276` | 1–12. |

No se propone dividir importaciones, errores o correcciones de estilo en commits artificiales. `contracts.py` agrupa sus modelos y validaciones porque las invariantes se necesitan juntas; `catalog.py` agrupa carga y validación global para no introducir un lector que acepte temporalmente configuraciones inválidas.

## Estado para revisión

El alcance de implementación #276 tiene evidencia funcional y pruebas reales; no depende de que se implementen #277–#280. La revisión debe conservar la observación de los dos fallos preexistentes, sin atribuir una suite global verde a esta entrega. No se cierra ningún issue ni se crea PR automáticamente.
