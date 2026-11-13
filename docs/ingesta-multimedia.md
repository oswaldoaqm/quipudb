# Ingesta de carpetas multimedia

Implementación del issue #187, sección 2.4.1. Una carpeta se procesa archivo
por archivo con `extract_features` de #181; cada objeto válido se inserta con
su nombre, ruta absoluta y vector. Los archivos defectuosos se reportan sin
detener los demás. No se cambió el contrato de SQL ni de carga CSV.

## Preparar un ejemplo pequeño

Requiere los bindings actuales y `pip install -r requirements.txt`. Desde la
raíz del proyecto, este ejemplo genera cuatro imágenes distintas y entrena
un vocabulario pequeño **solo para la demostración**:

```python
from pathlib import Path

import cv2
import numpy as np

from engine.multimedia.features import build_vocabulary, describe
from engine.multimedia.sift import read_grayscale

root = Path("datos/multimedia")
folder = root / "fotos_demo"
folder.mkdir(parents=True, exist_ok=True)
base = read_grayscale(Path("engine/multimedia/datos/ejemplo.png"))
random = np.random.default_rng(187)
descriptors = []
for i in range(4):
    noise = random.integers(-30, 31, base.shape)
    image = np.clip(base.astype(np.int16) + noise, 0, 255).astype(np.uint8)
    success, encoded = cv2.imencode(".png", image)
    assert success
    path = folder / f"foto_{i}.png"
    path.write_bytes(encoded.tobytes())
    descriptors.append(describe(path, max_keypoints=500))
build_vocabulary(descriptors, k=8, seed=187).save(root / "fotos_demo.npz")
(folder / "defectuosa.png").write_bytes(b"no es una imagen")
```

Para un dataset real, el vocabulario debe entrenarse sobre sus objetos y
reutilizarse **sin cambiar centroides ni IDF** al cargar y consultar. No debe
entrenarse un vocabulario por archivo ni por lote. Imágenes y audios usan
vocabularios separados: sus descriptores tienen dimensión 128 y 13,
respectivamente. `VECTOR(K)` guarda el histograma de K palabras, no la matriz
de descriptores. La carga no entrena un vocabulario: hay que entrenarlo antes,
desde Python como arriba o por REST, como explica la sección siguiente.
Las consultas deben usar también los mismos límites de puntos SIFT y
duración de audio que la ingesta para describir los objetos de igual forma.
El [manual de SIMILAR_TO](consultas-multimedia.md) explica cómo configurar el
mismo vocabulario y crear los índices IVF/HNSW desde SQL después de cargar.

## Entrenar el vocabulario por REST (#287)

Con la API levantada, el vocabulario de una carpeta del servidor se entrena sin
Python. Es un trabajo en segundo plano, como la carga:

```bash
curl -X POST http://localhost:8000/vocabularies \
  -H 'Content-Type: application/json' \
  -d '{"folder":"fotos_demo","k":8,"seed":187,"table":"fotos"}'
```

Devuelve 202 con un `job_id`; `GET /vocabularies/{job_id}` da el avance hasta
que `status` sea `completed` o `failed`. El trabajo recorre la carpeta, describe
cada archivo con SIFT o MFCC según su extensión, corre k-means y guarda el
resultado en `vocabularios/<nombre>.npz` dentro de `QUIPUDB_MEDIA_ROOT`. Si ese
nombre ya existe agrega `-2`, `-3`, y nunca pisa uno anterior.

| Campo | Por omisión | Qué hace |
|---|---|---|
| `folder` | obligatorio | Carpeta dentro de `QUIPUDB_MEDIA_ROOT` |
| `k` | 64 | Palabras del vocabulario; es la `K` de `VECTOR(K)` |
| `seed` | 0 | Semilla de k-means; con la misma semilla sale el mismo vocabulario |
| `name` | `<carpeta>-k<K>` | Nombre del archivo, solo letras, dígitos, `-` y `_` |
| `table`, `column` | ninguno | Si vienen, al terminar el vocabulario queda asociado a esa tabla |
| `max_files` | 500 | Con más archivos, toma esa cantidad espaciados en orden alfabético |
| `max_descriptors` | 50 000 | Muestra de descriptores para k-means; el IDF se mide con todos |
| `recursive`, `max_file_bytes`, `max_keypoints`, `max_audio_seconds` | como en la carga | Mismos límites que la ingesta |

La respuesta cuenta los archivos encontrados (`discovered`), los que no son
imagen ni audio (`skipped`), los elegidos (`selected`), los descritos
(`described`) y los que fallaron, con su error. Al terminar trae la ruta del
vocabulario, su huella (`codebook_id`), de cuántos objetos salió (`documents`) y
la tabla a la que quedó asociado (`registered_for`).

Antes de describir nada se rechaza, con 400 o 404, una carpeta fuera de la raíz,
una tabla que no existe, una columna que no es VECTOR, una tabla con varias
columnas VECTOR sin `column`, y una `k` distinta de la dimensión de la columna.
El trabajo falla si la carpeta mezcla imágenes y audios, porque sus descriptores
tienen 128 y 13 valores, o si salen menos descriptores que `k`. Hay un solo
entrenamiento activo por proceso; otro mientras tanto responde 409. Describir y
correr k-means no toma el lock del motor, así que las consultas siguen.

El entrenamiento guarda en memoria los descriptores de los archivos elegidos:
con `max_files=500` y 500 puntos SIFT por imagen son hasta 128 MB.

### Asociar un vocabulario a una tabla

`table` en el entrenamiento, o después:

```bash
curl -X PUT http://localhost:8000/tables/fotos/vocabulary \
  -H 'Content-Type: application/json' \
  -d '{"vocabulary":"vocabularios/fotos_demo-k8.npz"}'
```

Con `column` se asocia a una columna concreta, como la clave `tabla.columna` de
`QUIPUDB_MEDIA_VOCABULARIES`. La asociación vale de inmediato para `SIMILAR_TO`,
sin reiniciar la API, y se guarda en `vocabularios.json` dentro de la raíz, así
sobrevive al reinicio. Las de `QUIPUDB_MEDIA_VOCABULARIES` siguen valiendo; si
una tabla está en los dos lugares, gana la de `vocabularios.json`.

`GET /vocabularies` lista los `.npz` de la raíz y de `vocabularios/` con su K,
modalidad, cantidad de objetos y las tablas que los usan.

## Iniciar la API y crear la tabla

```bash
export PYTHONPATH="$PWD/build-py/bindings:$PWD"
export QUIPUDB_MEDIA_ROOT="$PWD/datos/multimedia"
python -m uvicorn engine.api.main:app --port 8000
```

Desde el editor SQL conectado al motor real, o `POST /query`:

```sql
CREATE TABLE fotos (
    id INT PRIMARY KEY,
    nombre VARCHAR(128),
    ruta VARCHAR(1024),
    rasgos VECTOR(8)
) USING HEAP;
```

La tabla debe tener exactamente esas cuatro funciones: PK `INT`, nombre y
ruta `VARCHAR`, y un `VECTOR` de dimensión K. Se admite cualquier orden de
columnas y otros nombres mediante `columns`. Se comprueban longitudes en
**bytes UTF-8**, componentes finitas de float32 y claves duplicadas. No se
truncan nombres ni rutas. `VECTOR` es palabra reservada: use `rasgos` u otro
identificador como nombre de columna.

## Contrato REST

```bash
curl -X POST http://localhost:8000/tables/fotos/load-media \
  -H 'Content-Type: application/json' \
  -d '{"folder":"fotos_demo","vocabulary":"fotos_demo.npz","batch_size":32}'
```

Devuelve **202**, sin esperar a extraer el dataset. Copie el `job_id` de la
respuesta y consulte esta ruta sustituyendo `JOB_ID`:

```bash
curl http://localhost:8000/tables/fotos/load-media/JOB_ID
```

El ejemplo termina con `inserted: 4`, `failed: 1` y el error de
`defectuosa.png`. `completed` significa que terminó el recorrido, no que
todos los archivos fueron válidos. `failed` como **estado del trabajo**
indica un problema global, por ejemplo un vocabulario inválido, una dimensión
incompatible o una tabla que dejó de estar disponible.

| Parámetro | Valor por defecto | Significado |
|---|---|---|
| `folder` | obligatorio | Carpeta del servidor, relativa a `QUIPUDB_MEDIA_ROOT` o absoluta dentro de ella |
| `vocabulary` | obligatorio | Archivo `.npz` completo, con codebook e IDF de #181 |
| `columns` | `{"id":"id","name":"nombre","path":"ruta","vector":"rasgos"}` | Asignación de nombres a columnas existentes |
| `start_id` | 1 | Primer identificador; debe estar libre y caber en INT de 32 bits |
| `batch_size` | 32 | Máximo de vectores pendientes de escribir; entre 1 y 256 |
| `recursive` | false | Incluir subcarpetas, sin seguir enlaces simbólicos |
| `max_file_bytes` | 33554432 | Máximo de bytes codificados por archivo; configurable hasta 64 MiB |
| `max_keypoints` | 500 | Máximo de descriptores SIFT por imagen; hasta 5000 |
| `max_audio_seconds` | 30 | Duración máxima analizada de cada audio; hasta 300 segundos |

La respuesta de inicio y la consulta de progreso usan los mismos campos:

- `job_id`, `table`, `status`: `pending`, `running`, `completed`, `failed` o
  `cancelled` (al cerrar ordenadamente el servidor).
- `discovered`: archivos encontrados hasta ahora; no es el total anticipado.
- `processed`, `inserted`, `failed`: resultados ya resueltos. Puede haber un
  lote pendiente, por eso `processed` puede ser menor que `discovered`.
- `errors`: hasta 100 pares de `path` y `error`; `errors_truncated` indica
  si hubo más. Se cuentan todos los fallos sin acumular una lista ilimitada.
- `next_id`: siguiente ID después de los archivos encontrados, incluidos
  fallos. Puede haber huecos y el orden de recorrido no está garantizado.
- `elapsed_ms`: tiempo desde que se aceptó el trabajo; se congela al terminar.
- `codebook_id`, `dimension`: huella de centroides y K del vocabulario leído.
  La huella no reemplaza la necesidad de conservar el archivo con su IDF.
- `error`: problema global si el trabajo no pudo completarse.

Una segunda carga activa devuelve **409**, evitando una cola ilimitada de
trabajos costosos. Una tabla o trabajo inexistente devuelve **404**; rutas y
esquemas incorrectos, **400**; parámetros mal formados, **422**, con el mismo
campo `error` que usa la API existente.

## Memoria y concurrencia

Se recorre con `os.scandir` sin construir una lista del dataset. Se mantiene
un vocabulario y, como máximo, un archivo en extracción más `batch_size`
vectores. El límite de bytes por archivo acota su entrada codificada, no
garantiza un límite exacto de la memoria del decodificador de imágenes.
Los extractores reciben límites de puntos SIFT y duración de audio.

El trabajo corre en un hilo dedicado. No se mantiene `Motor.en_uso` durante
la extracción: solo durante la validación de metadatos y cada lote de
escritura. SQL puede intercalarse entre lotes; consultar el progreso no toma
el lock del motor. La inserción reutiliza `insert_with_indexes`, incluidos
los índices secundarios y su reversión de mejor esfuerzo ante errores.

No se admite una transacción SQL activa al iniciar ni al escribir un lote.
Si otro cliente abre una transacción durante la extracción, la carga puede
fallar al llegar a su siguiente lote, sin confirmar ni deshacer la
transacción de ese cliente. Las filas de lotes previos permanecen.

## Alcance y seguridad

- Son carpetas **del servidor**, no una carga de directorios desde el
  navegador. Este issue añade REST y no un botón nuevo en el frontend.
- Raíz por defecto: `datos/multimedia`, relativa al directorio de inicio de
  la API. Solo el administrador la cambia con `QUIPUDB_MEDIA_ROOT`.
  No exponga esta API local sin autenticación a redes no confiables.
- Se rechazan rutas fuera de esa raíz y enlaces simbólicos, tanto del
  directorio como de cada archivo. La raíz debe estar administrada por el
  servidor; estas comprobaciones no aíslan carreras contra un proceso local
  que reemplace archivos mientras se leen.
- La carga es **parcial**, no atómica. Un archivo defectuoso no detiene los
  demás. Extensiones desconocidas también se reportan como fallos.
- Un objeto sin descriptores conserva el vector nulo definido por #181 y
  cuenta como insertado; no se descarta silenciosamente. Un archivo roto
  sigue siendo un fallo. La utilidad del vector nulo depende de la métrica.
- Hay un trabajo activo y hasta 32 reportes recientes por proceso. Los
  reportes no son persistentes, y este mecanismo requiere un único proceso
  de API (`--workers 1`, valor por defecto).
- Al reiniciar no se reanuda automáticamente ni se deduplican rutas. Las
  filas ya insertadas permanecen. Para dividir datasets, cargue carpetas
  disjuntas por lotes y use el `next_id` del trabajo terminado como nuevo
  `start_id`, conservando el mismo vocabulario. Reenviar la misma carpeta
  con otros IDs puede duplicar objetos.

## Verificación

```bash
ruff check engine benchmarks
PYTHONPATH="$PWD/build-py/bindings:$PWD" python -m pytest \
  engine/executor/test_media_load.py engine/api/test_media_load.py \
  engine/api/test_media_native.py -q
```

Las pruebas cubren imágenes y audio reales, persistencia, índices, un archivo
defectuoso entre objetos válidos, vectores nulos, columnas personalizadas,
errores acotados, seguridad de rutas y lotes de memoria limitada. Una prueba
retiene deliberadamente la extracción y comprueba que SQL y la consulta de
progreso responden antes de liberarla. Otra recorre 3000 archivos de forma
perezosa y comprueba que nunca se excede el lote configurado.
