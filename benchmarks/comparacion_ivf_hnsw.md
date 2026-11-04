# Comparacion preliminar IVF y HNSW — Issue #191

Mediciones locales del 4 de noviembre de 2026, macOS arm64. Se usaron los
seis paquetes sinteticos de #190, con vectores float32 de 64 componentes.
No se volvieron a extraer imagenes ni audios. Los catalogos originales se
conservaron: cada experimento cargo una tabla nueva.

Cada caso usa 100 consultas tomadas del corpus con semilla 20260906. Se
excluye el propio ID, conservando otros registros con el mismo vector.
IVF y HNSW consultan exactamente las mismas entradas y se comparan con
`scan_k_nearest_vector`. Todos utilizan coseno. El protocolo, limites y
comandos estan en la seccion #191 del README.

Las busquedas tienen un calentamiento y cinco repeticiones. Las latencias
resumidas son la mediana de cinco medias de 100 consultas. La construccion
se midio una sola vez; sus tiempos no incluyen cargar el Heap y no permiten
estimar dispersion. IVF total incluye entrenar y guardar los centroides.
Se usaron M=16 y ef_construction=200 para HNSW, y nlist=32/100/316 para IVF.

Las fuentes estan en `benchmarks/datasets/ivf_hnsw_191/`: CSV por consulta,
JSON de construccion y memoria, IDs consultados, parametros, hashes y logs.
Son archivos ignorados, disponibles localmente; otra copia del repositorio
debe reproducir los comandos o recibir esos archivos. `resumir_vectoriales.py`
genera el CSV agregado sin volver a ejecutar experimentos. No se generaron
graficas de #193.

## Construccion y almacenamiento

Tiempos en segundos, archivos en MB decimales y RSS en MiB. El RSS incluye el proceso completo.

| Dataset | IVF entrenamiento | IVF indice | IVF total | HNSW total | IVF / HNSW MB | RSS build IVF / HNSW |
|---|---:|---:|---:|---:|---:|---:|
| audio_1000 | 0.144 | 0.012 | 0.156 | 0.185 | 0.34 / 0.42 | 53.8 / 45.9 |
| audio_10000 | 1.826 | 0.146 | 1.972 | 2.242 | 2.98 / 4.15 | 205.5 / 59.2 |
| audio_100000 | 3.501 | 2.187 | 5.688 | 37.369 | 27.99 / 41.42 | 234.7 / 123.9 |
| imagenes_1000 | 0.161 | 0.006 | 0.168 | 0.207 | 0.36 / 0.42 | 54.1 / 45.2 |
| imagenes_10000 | 2.967 | 0.125 | 3.092 | 5.310 | 2.92 / 4.15 | 203.2 / 62.0 |
| imagenes_100000 | 3.541 | 2.105 | 5.646 | 117.687 | 28.02 / 41.42 | 228.0 / 111.5 |

## Consultas

Para esta tabla se selecciono, despues de medir, la configuracion mas rapida con Recall estricto >= 0,99. No es una configuracion optima para otras consultas. El CSV conserva todo el barrido, incluido Recall con empates.

| Dataset | Exacto ms | IVF nprobe | IVF ms | Recall IVF | HNSW ef | HNSW ms | Recall HNSW |
|---|---:|---:|---:|---:|---:|---:|---:|
| audio_1000 | 1.952 | 4 | 0.064 | 1.000 | 10 | 0.025 | 0.999 |
| audio_10000 | 21.892 | 4 | 0.181 | 1.000 | 10 | 0.030 | 1.000 |
| audio_100000 | 226.449 | 2 | 0.375 | 0.998 | 10 | 0.036 | 0.995 |
| imagenes_1000 | 1.886 | 32 | 0.417 | 1.000 | 40 | 0.099 | 0.997 |
| imagenes_10000 | 18.495 | 64 | 2.461 | 0.999 | 80 | 0.272 | 1.000 |
| imagenes_100000 | 223.860 | 64 | 11.918 | 0.993 | 160 | 0.686 | 0.994 |

## Barrido completo

Latencias en ms. R es Recall estricto; RT acepta empates en el umbral.

| Dataset | Indice | Parametro | ms | R | RT | RSS busqueda MiB |
|---|---|---:|---:|---:|---:|---:|
| audio_1000 | exacto | 0 | 1.952 | 1.000 | 1.000 | 45.6 |
| audio_1000 | ivf | 1 | 0.027 | 0.921 | 0.921 | 46.5 |
| audio_1000 | ivf | 2 | 0.037 | 0.984 | 0.984 | 46.5 |
| audio_1000 | ivf | 4 | 0.064 | 1.000 | 1.000 | 46.5 |
| audio_1000 | ivf | 8 | 0.110 | 1.000 | 1.000 | 46.5 |
| audio_1000 | ivf | 16 | 0.218 | 1.000 | 1.000 | 46.5 |
| audio_1000 | ivf | 32 | 0.395 | 1.000 | 1.000 | 46.5 |
| audio_1000 | hnsw | 10 | 0.025 | 0.999 | 0.999 | 46.5 |
| audio_1000 | hnsw | 20 | 0.032 | 1.000 | 1.000 | 46.5 |
| audio_1000 | hnsw | 40 | 0.049 | 1.000 | 1.000 | 46.5 |
| audio_1000 | hnsw | 80 | 0.079 | 1.000 | 1.000 | 46.5 |
| audio_1000 | hnsw | 160 | 0.131 | 1.000 | 1.000 | 46.5 |
| audio_1000 | hnsw | 320 | 0.219 | 1.000 | 1.000 | 46.5 |
| audio_10000 | exacto | 0 | 21.892 | 1.000 | 1.000 | 49.1 |
| audio_10000 | ivf | 1 | 0.068 | 0.926 | 0.926 | 50.0 |
| audio_10000 | ivf | 2 | 0.108 | 0.982 | 0.982 | 50.0 |
| audio_10000 | ivf | 4 | 0.181 | 1.000 | 1.000 | 50.0 |
| audio_10000 | ivf | 8 | 0.320 | 1.000 | 1.000 | 50.0 |
| audio_10000 | ivf | 16 | 0.590 | 1.000 | 1.000 | 50.0 |
| audio_10000 | ivf | 32 | 1.150 | 1.000 | 1.000 | 50.0 |
| audio_10000 | ivf | 64 | 2.323 | 1.000 | 1.000 | 50.0 |
| audio_10000 | ivf | 100 | 3.630 | 1.000 | 1.000 | 50.0 |
| audio_10000 | hnsw | 10 | 0.030 | 1.000 | 1.000 | 53.3 |
| audio_10000 | hnsw | 20 | 0.038 | 1.000 | 1.000 | 53.3 |
| audio_10000 | hnsw | 40 | 0.052 | 1.000 | 1.000 | 53.3 |
| audio_10000 | hnsw | 80 | 0.080 | 1.000 | 1.000 | 53.3 |
| audio_10000 | hnsw | 160 | 0.129 | 1.000 | 1.000 | 53.3 |
| audio_10000 | hnsw | 320 | 0.217 | 1.000 | 1.000 | 53.3 |
| audio_100000 | exacto | 0 | 226.449 | 1.000 | 1.000 | 67.6 |
| audio_100000 | ivf | 1 | 0.242 | 0.960 | 0.960 | 72.7 |
| audio_100000 | ivf | 2 | 0.375 | 0.998 | 0.998 | 72.7 |
| audio_100000 | ivf | 4 | 0.624 | 0.999 | 0.999 | 72.7 |
| audio_100000 | ivf | 8 | 1.128 | 1.000 | 1.000 | 72.7 |
| audio_100000 | ivf | 16 | 1.999 | 1.000 | 1.000 | 72.7 |
| audio_100000 | ivf | 32 | 3.726 | 1.000 | 1.000 | 72.7 |
| audio_100000 | ivf | 64 | 7.082 | 1.000 | 1.000 | 72.7 |
| audio_100000 | ivf | 128 | 16.043 | 1.000 | 1.000 | 72.7 |
| audio_100000 | ivf | 316 | 42.173 | 1.000 | 1.000 | 72.7 |
| audio_100000 | hnsw | 10 | 0.036 | 0.995 | 0.995 | 109.2 |
| audio_100000 | hnsw | 20 | 0.047 | 0.997 | 0.997 | 109.2 |
| audio_100000 | hnsw | 40 | 0.069 | 0.997 | 0.997 | 109.2 |
| audio_100000 | hnsw | 80 | 0.105 | 0.997 | 0.997 | 109.2 |
| audio_100000 | hnsw | 160 | 0.166 | 1.000 | 1.000 | 109.2 |
| audio_100000 | hnsw | 320 | 0.283 | 1.000 | 1.000 | 109.2 |
| imagenes_1000 | exacto | 0 | 1.886 | 1.000 | 1.000 | 45.8 |
| imagenes_1000 | ivf | 1 | 0.029 | 0.341 | 0.341 | 46.8 |
| imagenes_1000 | ivf | 2 | 0.049 | 0.544 | 0.544 | 46.8 |
| imagenes_1000 | ivf | 4 | 0.083 | 0.753 | 0.753 | 46.8 |
| imagenes_1000 | ivf | 8 | 0.146 | 0.920 | 0.920 | 46.8 |
| imagenes_1000 | ivf | 16 | 0.279 | 0.987 | 0.987 | 46.8 |
| imagenes_1000 | ivf | 32 | 0.417 | 1.000 | 1.000 | 46.8 |
| imagenes_1000 | hnsw | 10 | 0.048 | 0.966 | 0.966 | 47.2 |
| imagenes_1000 | hnsw | 20 | 0.067 | 0.986 | 0.986 | 47.2 |
| imagenes_1000 | hnsw | 40 | 0.099 | 0.997 | 0.997 | 47.2 |
| imagenes_1000 | hnsw | 80 | 0.142 | 1.000 | 1.000 | 47.2 |
| imagenes_1000 | hnsw | 160 | 0.195 | 1.000 | 1.000 | 47.2 |
| imagenes_1000 | hnsw | 320 | 0.262 | 1.000 | 1.000 | 47.2 |
| imagenes_10000 | exacto | 0 | 18.495 | 1.000 | 1.000 | 47.6 |
| imagenes_10000 | ivf | 1 | 0.063 | 0.352 | 0.352 | 49.4 |
| imagenes_10000 | ivf | 2 | 0.106 | 0.508 | 0.508 | 49.4 |
| imagenes_10000 | ivf | 4 | 0.195 | 0.680 | 0.680 | 49.4 |
| imagenes_10000 | ivf | 8 | 0.357 | 0.809 | 0.809 | 49.4 |
| imagenes_10000 | ivf | 16 | 0.684 | 0.935 | 0.935 | 49.4 |
| imagenes_10000 | ivf | 32 | 1.306 | 0.985 | 0.985 | 49.4 |
| imagenes_10000 | ivf | 64 | 2.461 | 0.999 | 0.999 | 49.4 |
| imagenes_10000 | ivf | 100 | 3.648 | 1.000 | 1.000 | 49.4 |
| imagenes_10000 | hnsw | 10 | 0.066 | 0.924 | 0.924 | 54.6 |
| imagenes_10000 | hnsw | 20 | 0.097 | 0.959 | 0.959 | 54.6 |
| imagenes_10000 | hnsw | 40 | 0.157 | 0.986 | 0.986 | 54.6 |
| imagenes_10000 | hnsw | 80 | 0.272 | 1.000 | 1.000 | 54.6 |
| imagenes_10000 | hnsw | 160 | 0.517 | 1.000 | 1.000 | 54.6 |
| imagenes_10000 | hnsw | 320 | 1.084 | 1.000 | 1.000 | 54.6 |
| imagenes_100000 | exacto | 0 | 223.860 | 1.000 | 1.000 | 68.3 |
| imagenes_100000 | ivf | 1 | 0.231 | 0.258 | 0.258 | 70.9 |
| imagenes_100000 | ivf | 2 | 0.410 | 0.420 | 0.420 | 70.9 |
| imagenes_100000 | ivf | 4 | 0.828 | 0.590 | 0.590 | 70.9 |
| imagenes_100000 | ivf | 8 | 1.439 | 0.766 | 0.766 | 70.9 |
| imagenes_100000 | ivf | 16 | 2.774 | 0.909 | 0.909 | 70.9 |
| imagenes_100000 | ivf | 32 | 5.624 | 0.979 | 0.979 | 70.9 |
| imagenes_100000 | ivf | 64 | 11.918 | 0.993 | 0.993 | 70.9 |
| imagenes_100000 | ivf | 128 | 22.034 | 1.000 | 1.000 | 70.9 |
| imagenes_100000 | ivf | 316 | 40.075 | 1.000 | 1.000 | 70.9 |
| imagenes_100000 | hnsw | 10 | 0.091 | 0.843 | 0.843 | 108.7 |
| imagenes_100000 | hnsw | 20 | 0.137 | 0.895 | 0.895 | 108.7 |
| imagenes_100000 | hnsw | 40 | 0.231 | 0.947 | 0.947 | 108.7 |
| imagenes_100000 | hnsw | 80 | 0.446 | 0.984 | 0.984 | 108.7 |
| imagenes_100000 | hnsw | 160 | 0.686 | 0.994 | 0.994 | 108.7 |
| imagenes_100000 | hnsw | 320 | 1.377 | 0.999 | 0.999 | 108.7 |

## Lectura de los resultados y limites

En las seis corridas HNSW dio menor latencia que IVF entre las configuraciones
seleccionadas con Recall estricto >= 0,99. La diferencia no implica que sea
siempre mejor: construir HNSW costo mas tiempo y sus archivos fueron mayores.
En 100k imagenes la construccion fue 117,69 s frente a 5,65 s de IVF, mientras
que las consultas seleccionadas fueron 0,686 ms frente a 11,918 ms.

Audio alcanzo Recall alto con menos exploracion que imagenes. Por ejemplo,
IVF con nprobe=2 en 100k audio obtuvo 0,998, mientras que en imagenes obtuvo
0,420. Estos datos sinteticos tienen distribuciones distintas; no se debe
trasladar esa diferencia directamente a colecciones de voz o fotografias.

Recall estricto y Recall con empates coincidieron en todas las configuraciones
medidas. Esto no significa que no haya vectores repetidos: significa que la
regla alternativa no cambio los aciertos de estas consultas. Las pruebas
incluyen casos donde los dos valores si difieren y duplicados nativos donde
el propio registro queda fuera de los primeros once candidatos.

El pico de construccion mas alto fue 234,7 MiB, en IVF de 100k audio. Esa
medida incluye entrenamiento, bibliotecas y datos mapeados; no demuestra que
el indice IVF residente ocupe mas que el grafo HNSW. La tabla de busqueda
presenta el RSS de procesos nuevos para evitar arrastrar el pico de build.

Las seis ejecuciones terminaron sin alcanzar los limites. Se conservaron
44 000 mediciones individuales (incluidas las de busqueda exacta), 12
construcciones y todos los parametros del barrido. No quedaron configuraciones
solicitadas pendientes. No se midieron otras configuraciones de construccion
ni variacion entre semillas. Falta repetir construcciones si se necesita
estimar su dispersion; no se atribuye significacion estadistica a una sola.

Se uso el binding local compilado en Release, segun build-py/CMakeCache.txt.
El orden fijo, cache no controlada y actividad concurrente del equipo limitan
la comparacion de latencias. La seleccion de parametros se hizo sobre estas
mismas consultas; no constituye una evaluacion con consultas externas.

Validacion final: 63 pruebas aprobadas (siete nuevas de #191, las de #190 y
las de busqueda nativa IVF/HNSW/exacta), sin omisiones. Ruff 0.17.3 y
`git diff --check` pasaron. Se auditaron los CSV: cada configuracion y
repeticion contiene exactamente los mismos 100 IDs de consulta del dataset.
No se modificaron motor, core, bindings ni frontend.
