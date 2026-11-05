# ADR 0009 - Pipeline multimedia e índices vectoriales

- **Fecha:** 2026-11-05
- **Estado:** Decisiones implementadas; consolidación documental pendiente de revisión
- **Issue:** #195, sección 2.4.1

## Contexto

QuipuDB necesita comparar archivos de imágenes y audio mediante vectores de
dimensión fija y recuperar sus registros por similitud. Los descriptores
locales tienen cantidades variables por archivo; no pueden indexarse como
si cada matriz fuera un solo vector de tamaño constante.

Este ADR complementa el [ADR 0007](0007-extraccion-de-caracteristicas-multimedia.md)
y el [ADR 0008](0008-consultas-vectoriales-similar-to.md). Describe el estado
actual, incluidas las búsquedas IVF (#184), HNSW (#186), el puntaje SQL (#189)
y los experimentos #190–#194. No sustituye el historial de esos documentos.
La creación SQL de IVF ya reutiliza K-Means en Python: esa integración dejó
de ser una posibilidad futura como se planteaba en el ADR 0007.

## Alternativas consideradas

| Alternativa | Decisión y motivo |
|---|---|
| Comparar directamente matrices SIFT/MFCC | No es la representación del índice: varían en número de filas. Se cuantizan mediante un vocabulario común. |
| Contar palabras sin ponderarlas | El código admite TF raw y log; el pipeline utilizado aplica TF log e IDF para reducir el peso de palabras frecuentes en el corpus. |
| Usar solamente búsqueda exacta | Se conserva como referencia y respaldo, pero requiere examinar todos los vectores. |
| Usar solamente IVF o solamente HNSW | Se mantienen ambos: los experimentos muestran costos distintos de construcción, consulta y almacenamiento. |
| Reentrenar el vocabulario al consultar | Se descarta: cambiar centroides o IDF cambia el significado de las componentes. Se reutiliza el vocabulario de la carga. |

Esta comparación explica la decisión vigente; no atribuye al equipo ensayos
no registrados de otras técnicas o librerías.

## Decisión: extracción y representación

### Imágenes y audio

[extract_sift](../../engine/multimedia/sift.py) decodifica la imagen en escala
de grises y usa SIFT de OpenCV. Devuelve una matriz `N × 128` en float32.
La cantidad N depende de la imagen; el recorte de puntos es estable y aplica
un límite estricto encima del límite blando de OpenCV. En #190 y en la
consulta multimedia se permite un máximo de 500 puntos.

[extract_mfcc](../../engine/multimedia/mfcc.py) decodifica mediante soundfile,
promedia los canales y trabaja a la frecuencia del archivo. Aplica
preénfasis 0,97, ventanas Hamming de 25 ms, salto de 10 ms, espectro de
potencia, 26 filtros mel, logaritmo con piso 1e-10 y DCT-II ortonormal.
La FFT usa la siguiente potencia de dos que cubre la ventana. El resultado
predeterminado es `N × 13` en float32. No se incorporan coeficientes delta
ni etiquetas musicales. La consulta y la ingesta limitan la lectura a 30 s;
el extractor aislado permite omitir ese límite.

Los datasets #190 contienen PNG de 128 × 128 y WAV mono PCM16 de 16 kHz,
8 000 muestras (0,5 s). Son patrones de formas y tonos sintéticos, no fotos
de clases etiquetadas ni canciones. SIFT describe gradientes locales, sin
usar directamente el color; MFCC resume propiedades espectrales locales.
Estas representaciones permiten reutilizar el mismo tratamiento posterior,
pero no prueban semejanza semántica.

### Vocabulario, histograma y TF-IDF

[build_codebook](../../engine/multimedia/kmeans.py) implementa K-Means con
inicialización k-means++, semilla explícita, hasta 100 iteraciones y tolerancia
1e-4. Asigna por bloques de 8 192 y recoloca los centroides vacíos en puntos
mal representados. Puede muestrear descriptores antes de entrenar.

En #190 se usa semilla 20260906 y K=64 por modalidad y tamaño. El script
muestrea hasta 128 objetos y un máximo predeterminado de 8 192 descriptores
para entrenar; después calcula DF sobre todos los objetos y guarda los
vectores mediante un archivo mapeado. La semilla, opciones y versiones se
conservan en los metadatos. Esto hace reproducible el procedimiento dentro
del entorno registrado; no promete igualdad binaria entre versiones de
OpenCV o plataformas distintas.

[Vocabulary](../../engine/multimedia/features.py) asigna cada descriptor a
su centroide más próximo y cuenta las asignaciones en un histograma de K
componentes. [weights.py](../../engine/multimedia/weights.py) aplica:

- `tf(c) = 1 + log10(c)` si c > 0; cero si la palabra no aparece.
- `idf(w) = log10(Nobjetos / df(w))` si df > 0; cero para df=0.
- `v[w] = tf(c[w]) * idf(w)`, seguido de normalización L2.

DF cuenta objetos que contienen la palabra, no descriptores individuales.
Una palabra presente en todos los objetos tiene IDF cero. El vector final
no nulo tiene K=64 componentes float32 y norma aproximadamente uno. El
pipeline general devuelve cero ante `EmptyObjectError` o pesos todos nulos;
los scripts #190 rechazan vectores nulos y no finitos. Son políticas distintas,
no una garantía de que toda extracción produzca contenido indexable.

El vocabulario completo se guarda en `.npz`: formato, centroides, semilla,
identificador, IDF y número de documentos. El identificador del codebook
comprueba la forma y los bytes de sus centroides. El paquete #190 añade
SHA-256 del vocabulario y de `vectores.npy`; no depende solo de la dimensión.

### Dos agrupamientos diferentes

| Modelo | Entrada | Centroides en los experimentos | Función |
|---|---|---|---|
| Vocabulario SIFT | Descriptores de 128 componentes | 64 × 128 | Convertir cada imagen en histograma |
| Vocabulario MFCC | Descriptores de 13 componentes | 64 × 13 | Convertir cada audio en histograma |
| IVF | Histogramas finales de 64 componentes | nlist × 64 | Asignar vectores a listas de búsqueda |

Se comparte el algoritmo K-Means, no el modelo entrenado. Los centroides IVF
no sustituyen al vocabulario ni al IDF. Tampoco se supone que tengan norma
uno: el código ordena y asigna sus listas por distancia euclidiana, incluso
cuando compara los candidatos mediante coseno.

### Registro, carga y consulta

Cada registro multimedia de #190 relaciona `id INT PRIMARY KEY`, `nombre`,
`ruta` y `rasgos VECTOR(64)`. La ruta identifica el archivo externo; el heap
guarda el vector y los índices guardan otra copia junto con su RID. `VECTOR(d)`
ocupa 4d bytes de componentes y exige dimensión y valores finitos; el tipo
no normaliza automáticamente.

[load_media_batch](../../engine/executor/processor.py) es la interfaz de
carga reutilizada por [cargar_multimedia.py](../../benchmarks/scripts/cargar_multimedia.py).
El script valida el paquete precalculado y pasa los vectores mediante el
proveedor de la carga; no ejecuta `ExtractFeatures` ni crea índices. Reabre
el catálogo y comprueba las filas persistidas.

En SQL, `SIMILAR_TO` sí extrae el archivo de consulta con el vocabulario
configurado en `QueryProcessor`. Debe ser exactamente el utilizado en la
carga, incluido IDF. El catálogo no persiste una identidad de vocabulario
por columna: comprobar K no detecta un modelo diferente de igual dimensión.
Los benchmarks consultan directamente con vectores precalculados para excluir
extracción y lectura de archivos de sus mediciones.
