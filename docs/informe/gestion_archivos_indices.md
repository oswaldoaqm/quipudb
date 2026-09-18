# Gestión de archivos, índices y algoritmos externos

[Volver al informe de la Parte 1](parte_1.md).

**Borrador técnico basado en código y pruebas. Requiere aporte y revisión de
Oswaldo Alejandro Quispe Monzon**, responsable declarado de 2.1.1 y 2.1.2.
Esta atribución de responsabilidad no afirma autoría ni aprobación del texto.

## Contratos y organización física

[TableFile e Index](../../core/include/quipudb/catalog/table.hpp) separan la
organización de registros de los accesos secundarios por clave–RID.
[Database](../../core/src/catalog/database.cpp) abre recursos según el
[catálogo](../../core/src/catalog/catalog.cpp). Los índices secundarios solo
pueden asociarse a Heap; no se supone estabilidad de RIDs en tablas que mueven
registros al ordenar o reorganizar.

[Page](../../core/include/quipudb/storage/page.hpp) administra una cabecera de
8 bytes y un cuerpo. [DiskManager](../../core/src/storage/disk_manager.cpp)
lee, escribe y asigna páginas; la página 0 se reserva para metadatos.
El tamaño de página es configurable, no una propiedad universal de un algoritmo.
Los contadores de páginas representan operaciones instrumentadas del motor,
no necesariamente accesos físicos al SSD, debido a las capas de caché.

Los cursores recorren registros; los adaptadores `RecordSource` permiten que los
algoritmos externos consuman una fuente sin inventar RIDs. Los
[bindings](../../bindings/module.cpp) exponen estas capacidades a Python, pero
no hacen que todas ellas sean invocables mediante SQL.

## Heap File

**Propósito y estructura.** Guarda registros sin orden por clave en slots de ancho
fijo con un byte de estado. Mantiene una lista LIFO de páginas con espacio y un
mapa en memoria `std::map<Key, RID, KeyLess>`, reconstruido al abrir la tabla,
para unicidad y localización de modificaciones por clave.

**Inserción.** Valida el registro y la clave, toma una página libre o asigna otra,
busca un slot disponible dentro de ella, escribe y actualiza contadores, mapa y
metadatos. La lista evita recorrer todas las páginas para hallar espacio; eso no
convierte toda la inserción en O(1): existen comprobaciones del mapa y del slot.

**Lectura y modificación.** La búsqueda pública por clave recorre los registros,
aunque exista el mapa auxiliar usado por otras operaciones. El borrado localiza
el RID con ese mapa, marca libre el slot y actualiza la lista de páginas.
La actualización nativa conserva la clave. No se mueve el último registro de la
tabla para rellenar el hueco: se reutiliza el slot en inserciones posteriores.

**Límites.** No ofrece organización ordenada ni reorganización equivalente a la
del Secuencial. Los RIDs de otros registros no se desplazan por borrar un slot,
pero un RID borrado puede reutilizarse. Los índices secundarios requieren
mantenimiento explícito al modificar registros.

Fuentes: [Heap](../../core/src/storage/heap_file.cpp) y
[pruebas](../../core/tests/storage/heap_file_test.cpp).

## Archivo Secuencial Paginado

**Propósito y estructura.** Mantiene páginas principales ordenadas por clave,
enlazadas, y un overflow acotado por grupo. Los vectores en memoria de páginas y
primeras claves permiten ubicar el grupo por búsqueda binaria. Dentro de la
página principal se busca en orden; el overflow se examina linealmente.

**Inserción.** Ubica el grupo correspondiente y utiliza su espacio/overflow.
Cuando se llena el overflow, reúne y ordena el grupo y lo divide en páginas
principales, dejando margen para futuras inserciones. El recorrido combina
los registros vivos del grupo para mantener el orden lógico.

**Borrado y umbral.** La eliminación es lazy: deja tombstones. El desperdicio
es `eliminados / (vivos + eliminados)`, no el porcentaje de bytes libres de toda
la capacidad asignada. `remove()` reorganiza automáticamente cuando la razón
**supera** el umbral predeterminado 0.30, no al alcanzarlo exactamente.

**Reorganización.** `reorganize()` recorre incrementalmente los registros vivos,
escribe un archivo `.reorg` con llenado objetivo del 80%, sustituye el archivo
anterior y reconstruye las referencias de páginas y primeras claves. Elimina
tombstones y actualiza estadísticas de reorganización. Utiliza buffers por grupo
y página, además de claves por página; no materializa toda la tabla ni implica
memoria constante independiente del número de páginas.

La explicación anterior en [arquitectura.md](../arquitectura.md) que describe
materialización de todos los registros no refleja este recorrido incremental.
Aquí se sigue la implementación actual sin modificar aquel documento.

**Límites.** Cambian los RIDs; no admite los índices secundarios del catálogo.
La medición explícita de reorganización y la eliminación que la dispara son
operaciones distintas, tal como documenta el [experimento #39](../../benchmarks/comparacion_heap_secuencial.md).

Fuentes: [Secuencial](../../core/src/storage/sequential_file.cpp) y
[pruebas](../../core/tests/storage/sequential_file_test.cpp).

## B+ Tree: organización agrupada e índice no agrupado

**Estructura compartida.** [BPlusTree](../../core/src/index/bplus_tree.cpp) usa
nodos persistidos en páginas: internos con separadores e hijos, hojas con claves
y payloads, enlazadas para recorrer rangos. La capacidad depende del tamaño de
página, clave y payload; no hay un único orden constante para todos los esquemas.

**Búsqueda.** Desciende por separadores hasta una hoja. La igualdad localiza las
coincidencias; el rango continúa por hojas enlazadas. En el índice secundario
pueden repetirse claves: el recorrido debe incluir duplicados entre hojas.

**Inserción.** Inserta en la hoja; si desborda, divide y propaga un separador.
La división de nodos internos promueve una clave al padre y puede crear una
nueva raíz. Las hojas conservan sus enlaces.

**Eliminación.** Borra la entrada y, ante insuficiencia de ocupación, intenta
redistribuir con un hermano o fusionar. Actualiza los padres, puede reducir la
altura y reutiliza páginas liberadas. En índices, `remove(key)` y
`remove_one(key, RID)` tienen alcances distintos para claves duplicadas.

| Variante | Payload y acceso a registros | Consecuencia |
|---|---|---|
| B+ agrupado | La hoja almacena el registro; es la tabla | Orden físico por clave; no requiere Heap para recuperar el registro |
| B+ no agrupado | La hoja almacena RIDs hacia Heap | Añade archivo de índice y lecturas de la tabla para resultados completos |

**Límites de comparación.** Construir un índice secundario sobre una tabla ya
cargada no equivale a crear una tabla agrupada desde cero. Recuperar RIDs tampoco
equivale a recuperar registros. El core soporta estas variantes; el SQL actual
no incluye `CREATE INDEX` ni una opción de creación de tabla B+ agrupada.

Fuentes: [adaptador agrupado](../../core/src/index/bplus_clustered_table.cpp),
[no agrupado](../../core/src/index/bplus_unclustered_index.cpp),
[pruebas de árbol](../../core/tests/index/bplus_tree_test.cpp),
[borrado](../../core/tests/index/bplus_tree_delete_test.cpp),
[tabla agrupada](../../core/tests/index/bplus_clustered_table_test.cpp) e
[índice secundario](../../core/tests/index/bplus_unclustered_index_test.cpp).

## Extendible Hashing

**Propósito y estructura.** Índice secundario para igualdad. Aplica FNV-1a de
64 bits a la clave serializada y utiliza los bits bajos para consultar un
directorio con profundidad global; cada bucket tiene profundidad local.
El directorio se mantiene en memoria y se persiste en páginas.

**Inserción.** Localiza el bucket y añade clave–RID. Si se llena, lo divide según
el siguiente bit; duplica el directorio cuando la profundidad local alcanza la
global. Colisiones no separables, incluidas claves repetidas, pueden usar una
cadena overflow en lugar de duplicar indefinidamente el directorio.

**Borrado.** Retira la entrada y puede rellenar el hueco dentro de la página.
Intenta fusionar con el bucket compañero si tienen la misma profundidad local,
no tienen overflow y las entradas combinadas caben; actualiza referencias y
reduce el directorio cuando es posible. No aplica el umbral de desperdicio del
Secuencial a este algoritmo.

**Lectura y límites.** La igualdad revisa el bucket y su overflow. El costo puede
crecer por colisiones y distribución; no se garantiza una única lectura ni
tiempo constante en todos los casos. Devuelve RIDs y recuperar registros exige
acceso a Heap. `supports_range` es falso, los rangos no están soportados y el
recorrido no es ordenado: esas capacidades no se simulan en la comparación.

Fuentes: [hash](../../core/src/index/extendible_hash.cpp),
[adaptador](../../core/src/index/extendible_hash_index.cpp),
[pruebas del hash](../../core/tests/index/extendible_hash_test.cpp) y
[del índice](../../core/tests/index/extendible_hash_index_test.cpp).

## Ordenamiento externo

**Propósito.** Ordenar una fuente que puede superar los buffers disponibles.
Con un presupuesto de B páginas, requiere B ≥ 3.

1. Consume bloques que caben en memoria, aplica `stable_sort` y escribe runs.
2. Mezcla hasta B−1 runs mediante una cola de prioridad y un buffer de salida.
3. Repite las pasadas hasta obtener el flujo ordenado final.

Si la entrada cabe en memoria, evita escribir runs temporales. Los recursos
temporales se gestionan con RAII. El límite de buffers del algoritmo no implica
que todos sus consumidores tengan memoria acotada: el resultado SQL final puede
materializarse completo en Python.

Disponible en core/bindings y utilizado por `ORDER BY` en el ejecutor SQL.
Fuentes: [ordenamiento](../../core/src/external/external_sort.cpp) y
[pruebas](../../core/tests/external/external_sort_test.cpp).

## Agrupación externa

**Propósito.** Agrupar por clave y calcular `COUNT`, `SUM`, `MIN`, `MAX` y `AVG`.
Las estrategias HASH, SORT y AUTO no representan una única técnica interna.

1. HASH particiona en disco mediante hash y mantiene acumuladores por grupo en
   un `std::map`; el límite interno depende de grupos distintos, no solo de filas.
2. Si una partición excede el límite, intenta reparticionarla con otra semilla.
3. Si no puede avanzar dentro de sus límites, HASH forzado puede fallar;
   AUTO dispone de una alternativa por ordenamiento.
4. SORT ordena por la clave y agrega grupos contiguos. `SUM`/`AVG` usan suma
   compensada de Neumaier para reducir error numérico.

La salida reúne los grupos en memoria; no debe describirse todo el proceso como
de memoria constante. El ejecutor SQL utiliza esta infraestructura para su
subconjunto de `GROUP BY`, no para expresiones SQL arbitrarias.
Fuentes: [agrupación](../../core/src/external/external_group.cpp) y
[pruebas](../../core/tests/external/external_group_test.cpp).

## JOIN externo

**Propósito y contrato.** Equijoin INNER de dos fuentes con claves de tipos
compatibles. Conserva las combinaciones de duplicados y concatena columnas,
resolviendo colisiones de nombres en el esquema de salida.

- **Hash join externo:** particiona ambos lados de forma compatible. Procesa
  particiones con una estructura interna cuando caben; ante una partición grande
  puede procesar bloques y volver a recorrer su contraparte.
- **Index nested loop:** recorre el lado externo y consulta una vía indexada
  del interno, mediante índice secundario o clave de tabla agrupada/secuencial.
  No trata la búsqueda PK lineal de Heap como una sonda indexada elegible.
- La elección usa estimaciones de páginas y costo de sondas; no basta con que
  exista cualquier índice para seleccionar siempre index nested loop.

La interfaz produce un `RecordSource`. Es funcionalidad nativa expuesta por
bindings, **no soporte de SQL JOIN** en el parser actual. No existen corridas
oficiales de estos algoritmos externos en #39/#40; sus tests no se presentan
como resultados comparativos de rendimiento.

Fuentes: [JOIN](../../core/src/external/external_join.cpp) y
[pruebas](../../core/tests/external/external_join_test.cpp).

## Relación con los resultados experimentales

El [capítulo experimental](comparacion_experimental.md) conserva metodología,
gráficas y conclusiones observadas de archivos e índices. El espacio corresponde
a archivos reales después de flush, no a payloads estimados; los contadores no
son una medición completa de I/O físico ni memoria. Este capítulo explica los
algoritmos, pero no incorpora nuevos tiempos ni reinterpreta las pruebas unitarias
como benchmarks.
