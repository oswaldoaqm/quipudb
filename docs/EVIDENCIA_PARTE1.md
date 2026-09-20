# Evidencia de implementación — Parte 1: Base de Datos Relacional

Este documento recopila, requisito por requisito (según `Proyecto_Integrador_BD2.docx`,
sección 2.1), dónde vive el código que lo implementa y qué pruebas lo verifican. Los
enlaces son rutas relativas al repo; ábrelos directamente para revisar el archivo
completo.

Estado global: **2.1.1 a 2.1.6 implementados y probados** (core en C++, capa Python,
frontend en TypeScript y comparación experimental con sus gráficas).

---

## 2.1.1 Gestión de Archivos y Almacenamiento

### Heap File

Registros en páginas en orden de llegada, con reutilización de espacio libre.

- [`core/include/quipudb/storage/heap_file.hpp`](../core/include/quipudb/storage/heap_file.hpp) /
  [`core/src/storage/heap_file.cpp`](../core/src/storage/heap_file.cpp)
  - `HeapFile::insert` (líneas 136–191): antes de crecer el archivo, revisa la
    free-list (`free_head_`) y reutiliza un hueco existente (comentario explícito en
    líneas 143–145 referenciando el requisito 2.1.1).
  - `HeapFile::remove` (líneas 193–219): marca el slot como `kFree` y, si la página
    estaba llena, la reinserta en la cabeza de la free-list en O(1) (líneas 206–212).
  - `HeapFile::search` / `scan` / `range_search` (líneas 288–340): recorrido
    página a página sin reordenar — confirma que el orden es de llegada.
  - `check_free_list()` (líneas 70–95): valida al abrir el archivo que la free-list
    no tenga ciclos ni corrupción.
- Pruebas: [`core/tests/storage/heap_file_test.cpp`](../core/tests/storage/heap_file_test.cpp)

### Archivo Secuencial Paginado

Registros ordenados por clave, inserción ordenada, eliminación lazy y reorganización
por umbral de desperdicio.

- [`core/include/quipudb/storage/sequential_file.hpp`](../core/include/quipudb/storage/sequential_file.hpp) /
  [`core/src/storage/sequential_file.cpp`](../core/src/storage/sequential_file.cpp)
  - Directorio de `first_keys_` con búsqueda binaria: `group_of`,
    `lower_bound_in_page` (líneas 195–216).
  - `SequentialFile::insert` (líneas 357–459): mantiene el orden desplazando bytes
    dentro de la página (390–406), agrega página principal nueva si corresponde
    (408–425), o usa una página de overflow por grupo (427–458).
  - **Eliminación lazy**: `SequentialFile::remove` (líneas 465–485) solo marca el
    slot como `kDeleted`, sin compactar físicamente.
  - **Umbral de reorganización 30%**: constante
    `kDefaultWasteThreshold = 0.30` ([`sequential_file.hpp:152`](../core/include/quipudb/storage/sequential_file.hpp#L152)),
    junto con `kFillFactor = 0.80` (línea 154, las páginas nuevas se llenan al 80%
    para dejar espacio a inserciones). Se dispara automáticamente en
    `remove()` (línea 483): `if (deleted_ > 0 && wasted_ratio() > waste_threshold_) reorganize();`
  - `SequentialFile::reorganize()` (líneas 491–567): reescribe el archivo a un
    temporal (`.reorg`) grupo por grupo y reemplaza el original atómicamente
    (`disk_.replace_with(tmp)`), reconstruyendo el directorio y reseteando el
    contador de borrados.
  - `split_group()` (líneas 248–351): también reclama slots borrados
    oportunistamente cuando el overflow de un grupo se llena.
- Pruebas: [`core/tests/storage/sequential_file_test.cpp`](../core/tests/storage/sequential_file_test.cpp)
- Documento de auditoría específico de este requisito:
  [`docs/auditoria-2.1.1.md`](auditoria-2.1.1.md)

---

## 2.1.2 Estructuras de Indexación y Optimización

### Índice B+ agrupado (clustered)

- [`core/src/index/bplus_clustered_table.cpp`](../core/src/index/bplus_clustered_table.cpp)
  (líneas 16–60+): la tabla completa vive en las hojas del árbol; `insert` /
  `remove` / `update` / `search` / `range_search` delegan en `BPlusTree`.
- Lógica del árbol: [`core/src/index/bplus_tree.cpp`](../core/src/index/bplus_tree.cpp)
  - `BPlusTree::insert` (línea 317), `insert_in_parent` — propagación de split
    (línea 385), `merge_children` — merge en underflow (línea 684).
  - Orden y ocupación mínima calculados en el constructor según capacidad de página
    (líneas 31–66): `min_leaf_ = (order_+1)/2`, `min_internal_ = order_/2`.
- Pruebas: [`core/tests/index/bplus_clustered_table_test.cpp`](../core/tests/index/bplus_clustered_table_test.cpp),
  [`core/tests/index/bplus_tree_test.cpp`](../core/tests/index/bplus_tree_test.cpp),
  [`core/tests/index/bplus_tree_delete_test.cpp`](../core/tests/index/bplus_tree_delete_test.cpp)
  (cobertura dedicada a delete/merge).

### Índice B+ no agrupado (unclustered)

- [`core/src/index/bplus_unclustered_index.cpp`](../core/src/index/bplus_unclustered_index.cpp)
  (línea 33): guarda pares `(clave → RID)` en un `BPlusTree` no único
  (`unique=false`) que apunta a un `HeapFile`; el constructor (líneas 36–41)
  rechaza cualquier fuente de datos que no sea heap, porque solo un heap da RIDs
  estables.
- Pruebas: [`core/tests/index/bplus_unclustered_index_test.cpp`](../core/tests/index/bplus_unclustered_index_test.cpp)

### Índice Hash Dinámico (Extendible Hashing)

- [`core/include/quipudb/index/extendible_hash.hpp`](../core/include/quipudb/index/extendible_hash.hpp) /
  [`core/src/index/extendible_hash.cpp`](../core/src/index/extendible_hash.cpp)
  (más el wrapper para tablas [`extendible_hash_index.cpp`](../core/src/index/extendible_hash_index.cpp))
  - `duplicar_directorio()` (líneas 283–299): duplica el directorio y aumenta
    `global_depth_`.
  - `crecer()` (líneas 422–483): split de bucket — redistribuye entradas por el
    bit de hash, crea el bucket hermano, y solo duplica el directorio si
    `local == global_depth_`.
  - `encoger()` (líneas 622–664) y `reducir_directorio()` (líneas 666–689): merge
    de buckets hermanos y reducción del directorio a la mitad cuando ningún
    bucket necesita el bit superior.
  - `encadenar_overflow()` (línea 324): overflow chaining para claves duplicadas
    o no separables.
  - `check_invariants()` (líneas 843–920): auto-auditoría completa (consistencia
    del directorio, profundidades local/global, bits de hash por entrada).
  - Función de hash FNV-1a: `hash_of()` (líneas 24–53), con normalización
    explícita de `-0.0`/NaN para doubles.
- Pruebas: [`core/tests/index/extendible_hash_test.cpp`](../core/tests/index/extendible_hash_test.cpp),
  [`core/tests/index/extendible_hash_index_test.cpp`](../core/tests/index/extendible_hash_index_test.cpp)

### External Sorting (k-way merge) para ORDER BY

- [`core/include/quipudb/external/external_sort.hpp`](../core/include/quipudb/external/external_sort.hpp) /
  [`core/src/external/external_sort.cpp`](../core/src/external/external_sort.cpp)
  - Genera runs ordenados a archivos temporales (`ExternalSort::Temporal`, líneas
    85–90) y hace merge k-way con `std::priority_queue`.
  - `source_of()` (líneas 73–79): adapta tanto un cursor de tabla como un vector
    en memoria como fuente, para componerse después de un scan/filtro.
- Pruebas: [`core/tests/external/external_sort_test.cpp`](../core/tests/external/external_sort_test.cpp)
- Conexión con el motor de consultas: [`engine/executor/external.py`](../engine/executor/external.py),
  [`engine/executor/test_order_group.py`](../engine/executor/test_order_group.py)

### External Hashing / índices para GROUP BY y JOIN

- **GROUP BY**: [`core/include/quipudb/external/external_group.hpp`](../core/include/quipudb/external/external_group.hpp) /
  [`core/src/external/external_group.cpp`](../core/src/external/external_group.cpp)
  - `ExternalGroupBy` (constructor ~línea 79): soporta agrupar por índice o por
    partición hash externa según la estrategia elegida.
  - `SumaCompensada` (líneas 39–57): suma compensada (Neumaier/Kahan) para que
    SUM/AVG sean precisos, justificado en comentario (líneas 33–36) de cara a la
    comparación contra PostgreSQL de 2.1.6.
- **JOIN**: [`core/include/quipudb/external/external_join.hpp`](../core/include/quipudb/external/external_join.hpp) /
  [`core/src/external/external_join.cpp`](../core/src/external/external_join.cpp)
  - `SondaPorIndice` (líneas 44–80): hace probe usando cualquier `Index`
    disponible (B+ clustered/unclustered o hash extensible) en vez de siempre
    particionar por hash.
  - `kEstructuraHash = "external_hash"` (línea 18): ruta de external hash join
    puro cuando no hay índice usable.
  - `costo_medido()` (líneas 35–41): modelo de costo con costos por página medidos
    experimentalmente, ver [`docs/medir-condicion-join.cpp`](medir-condicion-join.cpp).
- Pruebas: [`core/tests/external/external_group_test.cpp`](../core/tests/external/external_group_test.cpp),
  [`core/tests/external/external_join_test.cpp`](../core/tests/external/external_join_test.cpp)
- Racional de diseño: [`docs/adr/0002-plan-de-ejecucion.md`](adr/0002-plan-de-ejecucion.md)

---

## 2.1.3 Procesamiento de Consultas SQL

Parser propio (lexer + gramática recursiva descendente) que cubre exactamente el
subconjunto pedido por el enunciado.

- Lexer: [`engine/parser/lexer.py`](../engine/parser/lexer.py), [`engine/parser/tokens.py`](../engine/parser/tokens.py)
- Gramática: [`engine/parser/parser.py`](../engine/parser/parser.py), clase `_Parser`,
  función `parse_sql()` (línea 88)
  - `_select()` (línea 243): `SELECT [* | col,... | agregado(...)] FROM tabla [WHERE ...] [GROUP BY col] [ORDER BY col [ASC|DESC]]`
    (GROUP BY se parsea antes que ORDER BY, líneas 256–261).
  - `_insert()` (línea 226): `INSERT INTO tabla VALUES (...)`.
  - `_delete()` (línea 340): `DELETE FROM tabla WHERE condicion` (WHERE es
    obligatorio, líneas 343–346 — no se permite DELETE sin condición).
  - `_begin_transaction()` / `_end_transaction()` (líneas 354–360):
    `BEGIN TRANSACTION` / `END TRANSACTION`.
  - `_condition()` (línea 362): comparaciones (`=, <, <=, >, >=`) y `BETWEEN ... AND`.
  - `_create_table()` (línea 140): `CREATE TABLE ... USING HEAP|SEQUENTIAL` (extra,
    no pedido explícitamente pero necesario para tener tablas que consultar).
  - `JOIN ... ON` con condición de igualdad: `_from_source()` construye un árbol
    de fuentes (`TableRef` / `JoinRef`) y el ejecutor lo resuelve con el
    `ExternalJoin` del core (issue #96).
  - Palabras clave explícitamente no soportadas (`LIKE`, `UPDATE`, `HAVING`, etc.)
    se rechazan con `SQLUnsupportedError` — decisión de alcance documentada en
    [`docs/adr/0003-procesamiento-consultas-sql.md`](adr/0003-procesamiento-consultas-sql.md).
- AST: [`engine/parser/ast.py`](../engine/parser/ast.py); semántica/binding:
  [`engine/parser/semantic.py`](../engine/parser/semantic.py), [`engine/parser/bound_ast.py`](../engine/parser/bound_ast.py)
- Planeamiento: [`engine/planner/plan.py`](../engine/planner/plan.py),
  [`engine/planner/optimizer.py`](../engine/planner/optimizer.py),
  [`engine/planner/native_catalog.py`](../engine/planner/native_catalog.py)
- Ejecución: [`engine/executor/processor.py`](../engine/executor/processor.py)
  (`QueryProcessor.execute`, dispatch en líneas 85–98),
  [`engine/executor/dml.py`](../engine/executor/dml.py) (INSERT/DELETE con
  mantenimiento de índices), [`engine/executor/operators.py`](../engine/executor/operators.py),
  [`engine/executor/predicates.py`](../engine/executor/predicates.py),
  [`engine/executor/result.py`](../engine/executor/result.py)
- Pruebas: [`engine/parser/test_parser.py`](../engine/parser/test_parser.py),
  [`test_lexer.py`](../engine/parser/test_lexer.py), [`test_ast.py`](../engine/parser/test_ast.py),
  [`test_semantic.py`](../engine/parser/test_semantic.py),
  [`test_semantic_select.py`](../engine/parser/test_semantic_select.py),
  [`test_semantic_delete.py`](../engine/parser/test_semantic_delete.py),
  [`test_errors.py`](../engine/parser/test_errors.py);
  [`engine/executor/test_select.py`](../engine/executor/test_select.py),
  [`test_delete.py`](../engine/executor/test_delete.py),
  [`test_processor.py`](../engine/executor/test_processor.py),
  [`test_native.py`](../engine/executor/test_native.py),
  [`test_result.py`](../engine/executor/test_result.py);
  [`engine/planner/test_optimizer.py`](../engine/planner/test_optimizer.py),
  [`test_plan.py`](../engine/planner/test_plan.py);
  [`engine/test_query_processor.py`](../engine/test_query_processor.py)
- Documento de planeamiento detallado (775 líneas):
  [`docs/procesamiento-consultas-sql-plan.md`](procesamiento-consultas-sql-plan.md)

---

## 2.1.4 Transacciones y Concurrencia

### BEGIN / END TRANSACTION

- Parseo: [`engine/parser/parser.py:354-360`](../engine/parser/parser.py#L354)
- Dispatch: [`engine/executor/processor.py:94-97`](../engine/executor/processor.py#L94),
  `QueryProcessor._begin_transaction()` (línea 113) y `_end_transaction()` (línea 119)
- Objeto de transacción: [`engine/transactions/transaction.py`](../engine/transactions/transaction.py),
  clase `Transaction` (línea 29) — mantiene un **undo log en memoria**
  (`_undo_actions`), ya que el core no tiene WAL (explicado en el docstring del
  módulo, líneas 1–9, y en [`docs/adr/0004-transacciones-begin-end.md`](adr/0004-transacciones-begin-end.md)).
  `commit()` (línea 107) descarta el undo log y libera locks; `rollback()`
  (línea 117) reproduce las acciones de undo en reversa y libera locks.

### Control de concurrencia (locks)

- [`engine/transactions/locks.py`](../engine/transactions/locks.py)
  - `LockMode` (SHARED/EXCLUSIVE, línea 29)
  - `TableLock` (línea 34): lock por tabla con `threading.Condition`; `acquire()`
    (línea 43) espera con timeout (líneas 44–53, estrategia de deadlock por
    timeout en vez de detección wait-for-graph, documentada en
    [`docs/adr/0005-lock-manager-tabla.md`](adr/0005-lock-manager-tabla.md)).
    `_try_acquire_locked()` (línea 55) implementa reentrancia por dueño y upgrade
    shared→exclusive.
  - `LockManager` (línea 81): un `TableLock` por tabla, creado en forma perezosa
    (`_lock_for`, línea 88); expone `acquire/release/release_all`.
  - Granularidad por tabla (no por registro), justificada en la ADR 0005 con
    referencia directa al material del curso (`material/05 Recuperación ante
    Fallos.pdf`, slides 39–62).
  - Enganchado a la ejecución de consultas en
    [`engine/executor/processor.py:139-164`](../engine/executor/processor.py#L139)
    (`_lock_or_abort`, `_release_autocommit`) — incluso las sentencias autocommit
    (fuera de una transacción explícita) toman y liberan locks.
- Pruebas: [`engine/transactions/test_locks.py`](../engine/transactions/test_locks.py),
  [`engine/transactions/test_transaction.py`](../engine/transactions/test_transaction.py)

### Simulación obligatoria con hilos (race conditions)

- [`engine/transactions/demo_concurrencia.py`](../engine/transactions/demo_concurrencia.py)
  - Corre el mismo ciclo `BEGIN/SELECT/DELETE/INSERT/END` en dos modos, sin tocar
    la lógica de negocio (docstring del módulo, líneas 1–24):
    - **`sin-lock`** (línea 156): cada hilo recibe su propio `LockManager`
      privado → sin coordinación real → reproduce **lost updates / race
      conditions**.
    - **`con-lock`**: un único `LockManager` compartido entre todos los hilos
      (según ADR 0005) → resultados correctos y reproducibles; también cubre
      deadlocks en upgrades shared→exclusive vía timeout + retry con jitter
      (líneas 85–101).
  - `run_mode()` (línea 139): lanza N hilos con `threading.Thread` +
    `threading.Barrier` (línea 158), cada uno ejecuta `_increment_once` (línea 53)
    varias veces, y al final compara `valor_final` contra `esperado` (líneas
    183–200) para reportar CORRECTO/INCORRECTO.
  - Uso por CLI (líneas 16–24):
    `python -m engine.transactions.demo_concurrencia --modo [sin-lock|con-lock|ambos]`
- Pruebas que demuestran el resultado automáticamente:
  - [`engine/transactions/test_demo_concurrencia.py`](../engine/transactions/test_demo_concurrencia.py):
    `test_modo_sin_lock_corre_y_reporta_incorrecto` verifica que `run_mode("sin-lock", ...)`
    devuelve `False` (la race condition ocurre sin locks);
    `test_modo_con_lock_corre_y_reporta_correcto` verifica que `run_mode("con-lock", ...)`
    devuelve `True` (correcto con locks). Esta es la prueba directa y automatizada
    de "el sistema las maneja correctamente".
  - [`engine/test_concurrency.py`](../engine/test_concurrency.py): pruebas más
    amplias del lock manager con hilos reales contra el core.
- Racional de diseño: [`docs/adr/0004-transacciones-begin-end.md`](adr/0004-transacciones-begin-end.md),
  [`docs/adr/0005-lock-manager-tabla.md`](adr/0005-lock-manager-tabla.md)

---

## 2.1.5 Interfaz de Usuario (Frontend)

Interfaz en React + TypeScript + Vite con los cuatro paneles en una sola pantalla.
Unas 1 980 líneas bajo [`frontend/src/`](../frontend/src/).

- **Panel de Archivos** — [`frontend/src/panels/FilesPanel.tsx`](../frontend/src/panels/FilesPanel.tsx):
  lista las tablas del catálogo y, al elegir una, muestra sus columnas con tipo,
  cuál es la clave primaria, la organización del archivo y sus índices.
- **Panel de Consultas** — [`frontend/src/panels/QueryPanel.tsx`](../frontend/src/panels/QueryPanel.tsx):
  editor Monaco con resaltado SQL, atajo Ctrl+Enter, y los errores del parser
  subrayando el fragmento exacto que falló (usa el `Span` que devuelve la API).
- **Panel de Resultados** — [`frontend/src/panels/ResultsPanel.tsx`](../frontend/src/panels/ResultsPanel.tsx):
  tabla con el tipo de cada columna y virtualización de filas, para que 10 000
  filas no monten 30 000 celdas en el DOM.
- **Panel de Plan de Ejecución** — [`frontend/src/panels/PlanPanel.tsx`](../frontend/src/panels/PlanPanel.tsx):
  dibuja el árbol de pasos genéricamente a partir de `op`, `structure`, `stats` y
  `children`, según el contrato del [ADR 0002](adr/0002-plan-de-ejecucion.md).

La frontera con el motor es única: [`frontend/src/api/client.ts`](../frontend/src/api/client.ts)
expone `executeQuery` y `listTables`, y ningún componente sabe si detrás hay una
API real o los datos falsos de [`frontend/src/api/mock/`](../frontend/src/api/mock/).
El contrato completo, comentado campo por campo, está en
[`frontend/src/api/types.ts`](../frontend/src/api/types.ts).

La API que lo alimenta vive en [`engine/api/`](../engine/api/) (issue #99):
`POST /query` y `GET /tables` en FastAPI, con la composición del catálogo
separada de HTTP en [`engine/api/service.py`](../engine/api/service.py) para poder
probarla sin levantar el servidor.

- Pruebas: [`engine/api/test_service.py`](../engine/api/test_service.py) (composición,
  sin bindings) y [`engine/api/test_main.py`](../engine/api/test_main.py) (HTTP contra
  el core compilado).
- Informe: [`docs/informe/interfaz_usuario.md`](informe/interfaz_usuario.md)
- Issues #33–#37 (frontend) y #99 (API).

---

## 2.1.6 Comparación Experimental de Técnicas

Banco de pruebas reproducible en [`benchmarks/`](../benchmarks/), unas 3 200 líneas
de Python entre scripts y sus pruebas.

- **Banco y ejecución** — [`benchmarks/scripts/banco_pruebas.py`](../benchmarks/scripts/banco_pruebas.py),
  [`benchmarks/scripts/ejecutar_benchmarks.py`](../benchmarks/scripts/ejecutar_benchmarks.py):
  medición repetible con los contadores reales de `OpStats`, no estimaciones.
- **Datasets** — [`benchmarks/scripts/generar_datasets.py`](../benchmarks/scripts/generar_datasets.py):
  genera los tamaños que pide el enunciado (1 000 / 10 000 / 100 000).
- **Gestión de archivos** — [`benchmarks/scripts/casos_archivos.py`](../benchmarks/scripts/casos_archivos.py):
  Heap File vs Archivo Secuencial Paginado en inserción, búsqueda por clave
  primaria, espacio en disco y reorganización.
  Resultados en [`benchmarks/comparacion_heap_secuencial.md`](../benchmarks/comparacion_heap_secuencial.md).
- **Índices** — [`benchmarks/scripts/casos_indices.py`](../benchmarks/scripts/casos_indices.py):
  B+ agrupado vs B+ no agrupado vs Hash Dinámico en igualdad, rango,
  construcción del índice, espacio adicional y mantenimiento con inserciones y
  eliminaciones. Resultados en [`benchmarks/comparacion_indices.md`](../benchmarks/comparacion_indices.md).
- **Gráficas** — [`benchmarks/scripts/generar_graficas.py`](../benchmarks/scripts/generar_graficas.py):
  produce las 15 figuras de [`docs/informe/graficas/`](informe/graficas/), en PNG y SVG.
- **Informe comparativo** — [`docs/informe/comparacion_experimental.md`](informe/comparacion_experimental.md),
  con la tabla resumen de ventajas y desventajas y las conclusiones sobre cuándo
  conviene cada estructura.
- Pruebas: [`benchmarks/test_banco_pruebas.py`](../benchmarks/test_banco_pruebas.py),
  [`benchmarks/test_casos_archivos.py`](../benchmarks/test_casos_archivos.py),
  [`benchmarks/test_casos_indices.py`](../benchmarks/test_casos_indices.py),
  [`benchmarks/test_generar_datasets.py`](../benchmarks/test_generar_datasets.py),
  [`benchmarks/test_generar_graficas.py`](../benchmarks/test_generar_graficas.py)
- Issues #38–#41.

Aparte del banco, dos programas de calibración que se conservan porque documentan
decisiones del optimizador contra medición y no contra folklore:

- [`docs/medir-condicion-join.cpp`](medir-condicion-join.cpp): la tabla de costo por
  página que usa `conviene_index_nested()` para elegir entre hash join e index
  nested loop.
- [`docs/medir-condicion-merge.py`](medir-condicion-merge.py): la calibración
  equivalente para la decisión merge/hash del GROUP BY.

---

## Documentos de referencia adicionales

- [`docs/arquitectura.md`](arquitectura.md) — arquitectura general del motor
  (capa C++ + capa Python).
- [`docs/adr/0001-lenguaje-y-arquitectura-del-core.md`](adr/0001-lenguaje-y-arquitectura-del-core.md) —
  por qué el core es C++ y por qué **no es thread-safe por diseño** (de ahí que el
  locking viva en `engine/transactions/`, no en el core).
- [`docs/auditoria-2.1.1.md`](auditoria-2.1.1.md) — auditoría dedicada al
  cumplimiento de 2.1.1.

## Resumen

| Requisito | Estado | Evidencia principal |
|---|---|---|
| 2.1.1 Heap File | ✅ Completo | [`heap_file.cpp`](../core/src/storage/heap_file.cpp) |
| 2.1.1 Archivo Secuencial | ✅ Completo | [`sequential_file.cpp`](../core/src/storage/sequential_file.cpp) |
| 2.1.2 B+ agrupado | ✅ Completo | [`bplus_clustered_table.cpp`](../core/src/index/bplus_clustered_table.cpp), [`bplus_tree.cpp`](../core/src/index/bplus_tree.cpp) |
| 2.1.2 B+ no agrupado | ✅ Completo | [`bplus_unclustered_index.cpp`](../core/src/index/bplus_unclustered_index.cpp) |
| 2.1.2 Hash Dinámico | ✅ Completo | [`extendible_hash.cpp`](../core/src/index/extendible_hash.cpp) |
| 2.1.2 External Sort/Group/Join | ✅ Completo | [`external_sort.cpp`](../core/src/external/external_sort.cpp), [`external_group.cpp`](../core/src/external/external_group.cpp), [`external_join.cpp`](../core/src/external/external_join.cpp) |
| 2.1.3 Parser SQL | ✅ Completo (subconjunto declarado) | [`parser.py`](../engine/parser/parser.py) |
| 2.1.4 Transacciones + locks + demo hilos | ✅ Completo | [`transaction.py`](../engine/transactions/transaction.py), [`locks.py`](../engine/transactions/locks.py), [`demo_concurrencia.py`](../engine/transactions/demo_concurrencia.py) |
| 2.1.3 JOIN con External Hashing | ✅ Completo | [`external_join.cpp`](../core/src/external/external_join.cpp), [`optimizer.py`](../engine/planner/optimizer.py) |
| 2.1.5 Frontend (4 paneles) | ✅ Completo | [`frontend/src/panels/`](../frontend/src/panels/) |
| 2.1.5 API REST | ✅ Completo | [`engine/api/main.py`](../engine/api/main.py) |
| 2.1.6 Comparación experimental | ✅ Completo | [`benchmarks/`](../benchmarks/), [`comparacion_experimental.md`](informe/comparacion_experimental.md) |
