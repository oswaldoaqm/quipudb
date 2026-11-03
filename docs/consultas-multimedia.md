# Consultas multimedia con SIMILAR_TO (#188)

La consulta acepta el SQL del enunciado y recorre el camino completo:
archivo -> `ExtractFeatures` -> vector TF-IDF -> HNSW, IVF o k-NN exacto ->
recuperación por RID -> filas y plan. Requiere el **motor real**, no el
simulador del frontend, y los bindings compilados.

## Preparación y vocabulario

Prepare y cargue la carpeta `fotos_demo` del
[manual de ingesta](ingesta-multimedia.md). Su tabla `fotos` tiene un
`VECTOR(8)` y la carga usa `fotos_demo.npz`.

Antes de arrancar la API configure el **mismo archivo de vocabulario** que
usó para cargar los vectores:

```bash
export PYTHONPATH="$PWD/build-py/bindings:$PWD"
export QUIPUDB_MEDIA_ROOT="$PWD/datos/multimedia"
export QUIPUDB_MEDIA_VOCABULARIES='{"fotos":"fotos_demo.npz"}'
python -m uvicorn engine.api.main:app --port 8000
```

La configuración es un objeto JSON `tabla: ruta`. Se admite también una
clave `tabla.columna`, que tiene prioridad sobre `tabla` si hay varios
vectores. Las rutas son del **servidor**, relativas a `QUIPUDB_MEDIA_ROOT`
o absolutas dentro de esa raíz; archivos de consulta y vocabularios no
pueden escapar ni atravesar enlaces simbólicos. Se limita cada archivo
codificado a 64 MiB; eso no limita la memoria interna del decodificador.

La consulta no entrena un vocabulario nuevo ni infiere uno por nombre.
`Vocabulary.load` recupera los centroides y el IDF existentes;
`extract_features` aplica SIFT a imágenes y MFCC a audio, cuantiza y produce
un histograma normalizado de dimensión K. Se comprueba que K coincida con
`VECTOR(K)` y que las componentes sean float32 finitas. Las consultas usan
500 puntos SIFT y 30 segundos de audio: cargue con esos mismos límites
(los predeterminados de #187). Imágenes y audio necesitan vocabularios
separados, porque sus descriptores locales tienen dimensiones diferentes.

**No cambie el `.npz` después de cargar.** El catálogo aún no asocia una
huella de vocabulario a cada columna: comprueba la dimensión, pero no puede
detectar otro vocabulario de la misma dimensión. La configuración debe
conservar el archivo exacto, incluido su IDF, también al reiniciar.

## Crear los índices desde SQL

Después de completar la carga:

```sql
CREATE INDEX fotos_hnsw ON fotos (rasgos) USING HNSW WITH METRIC=cosine;
CREATE INDEX fotos_ivf ON fotos (rasgos) USING IVF;
```

HNSW permite omitir `WITH METRIC`, en cuyo caso usa `cosine`. La métrica del
grafo queda fija y persistida; una consulta incompatible falla en su métrica,
sin ejecutar el extractor. Si hay varios HNSW sobre la columna, se elige uno
compatible por nombre en orden alfabético. Los parámetros de construcción
son los del core: M=16, efConstruction=200 y seed=0.

IVF entrena sus centroides **sobre los histogramas almacenados**, no sobre
los descriptores SIFT/MFCC. Usa una muestra reservoir uniforme de hasta
4096 vectores, K-Means con seed=188 y hasta 16 listas por defecto (menos
si no hay suficientes filas). Es una pasada por la tabla con memoria
acotada para la muestra. No admite una tabla vacía: primero cargue vectores.
El vocabulario visual y los centroides IVF son modelos distintos.

Ambos índices requieren una columna `VECTOR` sobre una tabla HEAP y se
construyen sobre las filas existentes. `CREATE INDEX` no se admite en una
transacción explícita. El core persiste los índices y sus parámetros;
`INSERT`, `DELETE`, carga multimedia y la reversión transaccional los
mantienen mediante el contrato común de índices secundarios.

## Buscar y comparar

Desde el editor SQL con `VITE_USE_MOCK=false` o `POST /query`:

```sql
SELECT * FROM fotos
WHERE SIMILAR_TO('fotos_demo/foto_0.png', k=10) USING HNSW WITH METRIC=cosine;

SELECT nombre, ruta FROM fotos
WHERE SIMILAR_TO('fotos_demo/foto_0.png', k=10) USING IVF WITH METRIC=cosine;

EXPLAIN SELECT * FROM fotos
WHERE SIMILAR_TO('fotos_demo/foto_0.png', k=10) USING HNSW WITH METRIC=cosine;

EXPLAIN ANALYZE SELECT * FROM fotos
WHERE SIMILAR_TO('fotos_demo/foto_0.png', k=10) USING IVF WITH METRIC=euclidean;
```

Si hay una única columna VECTOR se resuelve implícitamente. Para varias,
indíquela: `SIMILAR_TO(rasgos, 'fotos_demo/foto_0.png', k=10)`.
También se admite k posicional, por ejemplo `SIMILAR_TO('a.wav', 5)`.
`USING HNSW|IVF` es obligatorio; `WITH METRIC` es opcional, con `cosine`
por defecto. `euclidean`, `cosine` y `dot` no distinguen mayúsculas.

Las filas salen de mayor similitud a menor: distancia euclidiana creciente,
coseno decreciente o producto punto decreciente. El core transforma las tres
a una distancia interna donde menor es mejor. El vector cero es válido y
conserva la semántica definida por las métricas del core.

`k=0` devuelve vacío y k mayor que la tabla devuelve hasta las filas
disponibles. Los índices son aproximados: IVF con pocas listas consultadas
puede devolver menos de k, aunque existan más filas en otras listas. k se
acota por el tamaño de la tabla antes de cruzar a `size_t` en C++.

`USING IVF` nunca sustituye IVF por HNSW ni al revés. Si no existe el índice
pedido sobre esa columna, cae a `scan_k_nearest_vector`, incluso si hay un
índice del otro tipo. La búsqueda exacta también funciona sobre tablas
secuenciales, sin índices secundarios.

`ORDER BY ... LIMIT n` ordena **los k vecinos obtenidos** y corta después;
no convierte una búsqueda aproximada en exacta. Un `LIMIT` sin orden corta
el ranking de vecinos. Se rechazan JOIN, GROUP BY y DELETE con SIMILAR_TO;
`SCORE()` sigue perteneciendo exclusivamente a MATCH de texto.

## Plan y errores

La ruta indexada declara `knn_search` con `structure: "hnsw"` o `"ivf"`,
seguida de `fetch` con la estructura de la tabla. El fallback mantiene
`knn_search`, pero su estructura es `heap`, `sequential` o
`bplus_clustered`, nunca un índice ficticio, y el detalle indica búsqueda
exacta. El frontend lo distingue como recorrido completo.

El paso k-NN añade `metric: "euclidean"|"cosine"|"dot"`; los otros pasos
llevan `metric: null`. `Plan.from_dict` sigue leyendo planes antiguos sin
ese campo. Los contadores y tiempos de búsqueda y fetch son independientes.
En el scan exacto, los contadores nativos del cursor incluyen todas las
filas recorridas; el fetch recupera únicamente los vecinos. El tiempo
total del plan incluye además el coste de extracción.

`EXPLAIN` valida esquema, rutas, configuración y compatibilidad HNSW, pero
no ejecuta ExtractFeatures ni busca vecinos: contadores y tiempos de los
pasos quedan en cero. La dimensión/contenido del vocabulario se comprueban
al extraer. `EXPLAIN ANALYZE` sí extrae, busca y mide, sin devolver las filas.

Estructuras y métricas desconocidas son errores sintácticos ubicados.
Archivos inexistentes, rutas prohibidas, vocabulario ausente/incompatible
y fallos del extractor se reportan como errores semánticos en el literal
del archivo. La API conserva su respuesta 400 con `line` y `column`.

## Uso directo en Python y parámetros

```python
from pathlib import Path

import quipudb_native as q

from engine.executor import QueryProcessor

processor = QueryProcessor(
    q.Database("datos/catalogo.txt"),
    media_root=Path("datos/multimedia"),
    media_vocabularies={"fotos": "fotos_demo.npz"},
    ivf_nlist=16,         # construcción; se acota a la muestra disponible
    vector_nprobe=1,     # búsqueda IVF; el core lo acota a nlist
    vector_ef_search=64, # búsqueda HNSW; el core usa al menos k candidatos
)
result = processor.execute(
    "SELECT nombre FROM fotos WHERE "
    "SIMILAR_TO('fotos_demo/foto_0.png', k=3) USING HNSW WITH METRIC=cosine"
)
print(result.rows)
print(result.plan.to_dict())
```

Los tres parámetros deben ser enteros entre 1 y 65536. La API usa sus
predeterminados. Para comparar IVF con la línea base exacta, use nprobe
igual a nlist; aumentar nprobe o ef_search mejora la exploración a costa
de más comparaciones. No hay sintaxis SQL adicional para estos parámetros.
La búsqueda HTTP es síncrona y utiliza la serialización existente del motor;
no crea trabajos en segundo plano como la ingesta de #187.

## Verificación

```bash
ruff check engine benchmarks
python -m pytest engine/parser/test_similar.py engine/planner/test_similar.py \
  engine/executor/test_vector_search.py engine/executor/test_similar_native.py \
  engine/api/test_similar_native.py -q
```

Las pruebas nativas incluyen las tres métricas y las dos estructuras,
fallback, EXPLAIN/ANALYZE, persistencia, DML/rollback, límites, HTTP y
extracción real de las imágenes y audios pequeños versionados en el repo.
