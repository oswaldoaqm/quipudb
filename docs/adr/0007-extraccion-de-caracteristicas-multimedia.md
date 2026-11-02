# ADR 0007 - Extraccion de caracteristicas multimedia y libro de palabras

- **Fecha:** 2026-10-31
- **Estado:** Aceptada
- **Issues:** #176 (SIFT), #177 (MFCC), #178 (K-Means y codebook), #181 (histograma)

## Contexto

La seccion 2.4.1 del enunciado pide una funcion `ExtractFeatures(objeto)` que
convierta una imagen o un audio en un vector numerico, en tres pasos:
descriptores locales (SIFT para imagenes, MFCC para audio), cuantizacion con
K-Means para obtener K palabras, e histograma de esas palabras con TF-IDF.

El problema de fondo es que los descriptores locales **no tienen forma fija**:
una imagen da N descriptores de 128 valores y otra da M, con N distinto de M.
Dos matrices de distinto tamano no se pueden restar, asi que no hay distancia
directa entre dos objetos. El codebook es lo que los lleva a un espacio comun.

Este ADR reune las decisiones de los issues #176 a #181, con las mediciones
que respaldan cada una.

## Decision

### 1. La extraccion vive en `engine/multimedia/`, no en el core

| Decision | Por que |
|---|---|
| Paquete nuevo en Python, hermano de `parser`, `planner`, `executor`, `transactions` y `api` | Es procesamiento de archivos externos, no gestion de paginas: no toca disco del motor y no necesita los bindings para probarse |
| `FeatureExtractionError` en su propio modulo `errors.py` | Cada extractor arrastra su dependencia pesada. Si `mfcc.py` tomara el error de `sift.py`, extraer MFCC exigiria tener OpenCV instalado |
| Cada extractor devuelve una matriz de N x D en `float32` | Mismo contrato para imagen y audio: lo que sigue en el pipeline no necesita saber de cual vino |
| Un objeto sin descriptores es un **error**, no una matriz vacia | Un vector de ceros se indexa igual que cualquier otro y el objeto aparece como "parecido" a cosas que no se le parecen, sin que nada avise |

### 2. SIFT con OpenCV, pero con el recorte hecho aparte

Se usa `cv2.SIFT_create()` de `opencv-python-headless`. La variante headless
porque el CI corre sin pantalla y el paquete normal arrastra `libGL`. SIFT
esta en el modulo principal desde la 4.4, cuando vencio la patente de Lowe.

| Decision | Por que |
|---|---|
| El limite de puntos se aplica **encima** del `nfeatures` de OpenCV | `nfeatures` es un tope blando: `KeyPointsFilter::retainBest` conserva todos los puntos que empatan con la respuesta del ultimo que entra, y SIFT emite un punto por cada orientacion dominante de una misma ubicacion, todos con la misma respuesta. **Medido:** pedir 3 puntos devuelve 5 descriptores en la imagen de ejemplo |
| El desempate del recorte es estable | Dos extracciones de la misma imagen tienen que dar la misma matriz, o el codebook dejaria de ser reproducible |
| No se usa `cv2.imread` sino `imdecode` sobre los bytes leidos con `pathlib` | En Windows, `imread` devuelve `None` sin avisar cuando la ruta tiene tildes, y ese `None` es indistinguible del de un archivo que no es imagen: el mensaje culparia al formato cuando el problema es el nombre de la carpeta |

**No se prueba la invariancia a escala y rotacion.** La invariancia es del
*descriptor* --se calcula relativo a la orientacion dominante del punto--, no
del *detector*: cuantos puntos encuentra depende de la rejilla de pixeles y de
la piramide gaussiana. **Medido** sobre la imagen de ejemplo:

| transformacion | descriptores |
|---|---|
| original | 33 |
| rotada 90 grados | 36 |
| rotada 180 grados | 28 |
| espejada | 37 |
| escalada x2 | 232 |

Una prueba que exigiera el mismo numero se romperia con la proxima version de
OpenCV sin que nada estuviera mal.

### 3. MFCC implementado, no delegado a librosa

De fuera se usa solo `soundfile`, y unicamente para decodificar el archivo.
El pipeline esta escrito con numpy en `engine/multimedia/mfcc.py`:

```
preenfasis -> ventaneo Hamming -> |FFT|^2 -> banco de filtros mel -> log -> DCT
   0.97          25 / 10 ms                     26 bandas            13 coef.
```

| Decision | Por que |
|---|---|
| Implementarlo en vez de usar librosa | El enunciado dice "implementa" y nombra el algoritmo, no una libreria. El resto del motor --B+, R-Tree, SPIMI, BM25-- tambien esta escrito a mano. **Medido:** librosa arrastra numba, llvmlite, scikit-learn, scipy, soxr y pooch, unos 150 MB en el CI de todas las ramas del equipo; soundfile pesa 1 MB |
| Ventana de 25 ms con salto de 10 ms | Dentro de 25 ms el sonido se puede tratar como estacionario; el salto menor que la ventana hace que se solapen y que un sonido en un borde no se pierda |
| Escala mel y no hercios | El oido distingue bien 200 de 300 Hz y casi nada 9000 de 9100. Los centros se reparten uniformes en mel y recien despues se traducen a Hz, y por eso las bandas graves salen angostas y las agudas anchas |
| DCT-II **ortonormal** | Conserva la energia: dos audios parecidos en el espectro siguen parecidos despues de la DCT, y la distancia de #179 sigue significando lo mismo. Las bandas mel vecinas estan muy correlacionadas y la DCT concentra todo en los primeros coeficientes, por eso 13 alcanzan |
| Piso de `1e-10` antes del logaritmo | Una ventana en silencio da energia nula y `log(0) = -inf`. De ahi en adelante el K-Means propaga el infinito a su centroide y el histograma sale con NaN |
| Los canales se promedian | Un mismo sonido en estereo no deberia describirse distinto que en mono, o entraria dos veces al indice como si fueran dos cosas |
| La duracion analizada se acota **al leer** | Una cancion de cuatro minutos a 44,1 kHz son diez millones de muestras. Leer el archivo entero para tirar el 95 % seria el cuello de botella de un dataset de 100 000 objetos |

**Medido:** `libsndfile 1.2.2` lee WAV, FLAC, OGG y MP3, asi que el
`SIMILAR_TO('audio_query.mp3', k=5)` de la seccion 2.4.3 queda cubierto.

### 4. K-Means en Python, sobre una matriz generica

La nota de #178 pide que IVF (#183) y el Bag of Words compartan una sola
implementacion. `engine/multimedia/kmeans.py` trabaja sobre una matriz de
N x D cualquiera y no sabe nada de imagenes ni de audio, justamente para que
IVF pueda reusarlo.

| Decision | Por que |
|---|---|
| En Python y no en C++ con binding | Los descriptores nacen en Python (OpenCV y soundfile). Pasarlos al core por pybind11 para volver obligaria a mover matrices de gigabytes sin ganancia, y tocaria el area de #183, que no ha empezado. **Queda abierto:** si IVF se implementa en el core, esta es la implementacion de referencia a portar |
| Siembra con k-means++ | Al azar puro, dos centroides pueden caer en el mismo grupo y dejar otro sin representante: el vocabulario queda con palabras repetidas y con zonas del espacio vacias |
| Semilla explicita y resultado identico con la misma semilla | Si reconstruir el dataset cambiara los centroides, la palabra 7 pasaria a significar otra cosa y habria que reindexar todos los objetos |
| Un centroide que se queda sin puntos se recoloca en el punto peor representado | Dejarlo quieto lo congela para siempre y el vocabulario pierde una palabra en silencio |
| Muestreo opcional de descriptores, con la misma semilla | 100 000 imagenes a 500 descriptores de 128 valores son unos 25 GB. Los centroides describen zonas densas del espacio y esas zonas no desaparecen al muestrear |
| La asignacion trabaja por bloques de 8192 | Calcular las distancias de un millon de descriptores contra 256 centroides de una vez pediria gigabytes; por bloques el pico no depende del tamano del dataset |

### 5. El codebook se guarda con una huella

Formato 1, en `.npz`: `formato`, `centroids`, `seed`, `identifier`.

La huella es `blake2b` de 8 bytes sobre la forma y los bytes de los centroides.
Entra la forma ademas de los valores porque los mismos numeros como 2x4 o como
4x2 no son el mismo vocabulario y `tobytes` no los distingue.

| Decision | Por que |
|---|---|
| El codebook viaja a disco y se reutiliza | **Una consulta tiene que cuantizarse con el mismo codebook que el dataset.** Con otro, la palabra 7 significa otra cosa, los histogramas salen igual de bien formados y de la dimension correcta, y la busqueda devuelve resultados sin sentido sin avisar |
| La huella se **recalcula** al leer en vez de creerle a la guardada | Un archivo a medio escribir o editado a mano daria centroides plausibles |
| El numero de formato se guarda y se comprueba | Si cambia la forma de guardarlo, un archivo viejo se rechaza en vez de leerse mal |

### 6. El histograma usa las formulas TF-IDF de la Parte 3

`ExtractFeatures(objeto)` decide el pipeline por la extension del archivo,
cuenta en cuantos descriptores cae cada palabra del codebook y pesa el conteo.
Las formulas son **las mismas** que usa la busqueda de texto, definidas en
`core/include/quipudb/index/text_weights.hpp`:

    idf(w) = log10(N / df_w)        tf_w = 1 + log10(tf)  |  tf

| Decision | Por que |
|---|---|
| Las formulas de la Parte 3, no una variante propia | Si el histograma visual pesara distinto que el textual, los numeros del informe no se podrian comparar entre las partes 3 y 4, y nadie se enteraria hasta escribirlo |
| El `df` se cuenta por OBJETO, no por descriptor | Es lo mismo que en texto: importa en cuantos documentos esta un termino, no cuantas veces en total. Un objeto con cincuenta descriptores en la misma palabra la cuenta una vez |
| El vector sale normalizado a norma 1 | Con vectores normalizados el producto punto vale lo mismo que el coseno (ver `vector_metric.hpp`), asi que el indice se ahorra dos normas por comparacion |
| El pipeline se elige por la extension | Probar los dos extractores con cada objeto costaria el doble en el caso malo, y un error de OpenCV sobre un `.wav` no diria que el problema es el pipeline elegido |
| El IDF viaja con el codebook en el mismo archivo | El codebook dice QUE palabras hay y el IDF CUANTO pesa cada una. Aplicar uno con el IDF de otro dataset da histogramas bien formados y falsos |

**Un objeto sin descriptores devuelve el vector nulo**, no un error (criterio 4
de #181). Lo distingue `EmptyObjectError`, que los extractores lanzan solo
cuando leyeron bien el archivo y no habia nada que describir; un archivo roto
sigue siendo un error, porque un `.jpg` a medio descargar no es una foto en
blanco. Hay que saber lo que significa el vector nulo: bajo similitud del
coseno queda a **distancia 1 de todo, incluido de si mismo**, asi que esos
objetos no son alcanzables por una busqueda y conviene no indexarlos.
`tiene_contenido` existe para filtrarlos al armar el dataset.

**Un vocabulario demasiado chico colapsa los vectores.** Si K es menor que la
estructura real de los datos, casi toda palabra aparece en casi todos los
objetos, su idf es 0 y los histogramas salen nulos. **Medido** sobre doce
variantes de la imagen de ejemplo:

| K | palabras con idf 0 | vectores nulos |
|---|---|---|
| 8 | 6 de 8 | 1 de 12 |
| 32 | 9 de 32 | 0 de 12 |
| 64 | 6 de 64 | 0 de 12 |

No es un defecto del idf: es la senal de que el vocabulario hay que agrandarlo.

## Consecuencias

- `ExtractFeatures` recibe el vocabulario, no lo construye: la consulta y el
  dataset pasan por el mismo objeto y la huella permite comprobarlo.
- Quien arma el dataset decide que hacer con los objetos vacios. El pipeline
  les da un vector definido y los deja reconocer; no los descarta por su
  cuenta, porque eso es politica del dataset y no de la extraccion.
- El vocabulario es un **artefacto del dataset**. Reconstruirlo con otra
  semilla, otra muestra u otro K invalida todos los histogramas ya indexados.
- Si #183 implementa IVF en el core, hay que decidir si porta este K-Means a
  C++ o si el core llama al de Python. Esa decision no esta tomada aqui.
- Las pruebas de extraccion se saltan solas cuando falta `cv2` o `soundfile`,
  para no romperle la suite a quien no instalo las dependencias. El CI no
  puede saltarselas: comprueba que ambos importan antes de llamar a pytest.
