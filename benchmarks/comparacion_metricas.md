# Euclidiana y coseno sobre los vectores multimedia (#192)

Corrida local del 4 de noviembre de 2026, macOS arm64, bindings Release.
Se reutilizaron los seis datasets normalizados de #190 sin modificarlos.
Cada metrica usa los mismos 100 IDs elegidos con semilla 20260906, excluyendo
el propio registro. Hay un calentamiento y cinco repeticiones de consultas.
Los parametros son los de #191 y se mantienen iguales entre metricas.
HNSW se construye de nuevo con su metrica; IVF usa el mismo entrenamiento
euclidiano y semilla en ambas ejecuciones.

Las latencias de las tablas son medianas de cinco medias de 100 consultas.
Recall se calcula contra la referencia exacta de cada metrica. La coincidencia
es la interseccion de identidades de registros entre ambos Top 10, no igualdad
de las distancias ni una comparacion de relevancia humana. Los CSV conservan
ademas Recall con empates y los RIDs individuales.

Las fuentes locales estan en `benchmarks/datasets/metricas_192/`, ignoradas
por Git: resultados individuales por metrica, comparacion por consulta,
resumen por configuracion, parametros, memoria, construccion y logs.
Otra copia del repositorio debe ejecutar los comandos del README o recibir
esas fuentes. No se generaron las graficas de #193.

La desviacion maxima de la norma L2 respecto de 1, calculada en float64 sobre
los float32 originales, fue 4,60e-8. No se renormalizo ni perturbo ningun vector.
Para normas exactamente unitarias se cumple d²=2-2*coseno y el ranking teorico
es equivalente. Redondeo, empates y navegacion aproximada deben distinguirse
de una diferencia matematica entre metricas.

La tabla principal usa nprobe=16 y ef_search=80 para todos los tamanos.
No se seleccionaron parametros diferentes para favorecer una metrica.

## Resultados finales

Las doce ejecuciones por metrica terminaron. Al reanudar se verificaron sus
archivos; no se repitieron benchmarks. Hay 88 000 mediciones individuales y
44 000 comparaciones emparejadas.

### Configuraciones principales

| Dataset | Indice | Parametro | Euclidiana ms | Coseno ms | Recall E | Recall C | Comunes | % |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| audio_1000 | ivf | 16 | 0.244 | 0.377 | 1.000 | 1.000 | 10.00 | 100.0 |
| audio_1000 | hnsw | 80 | 0.118 | 0.159 | 1.000 | 1.000 | 10.00 | 100.0 |
| audio_10000 | ivf | 16 | 0.572 | 0.666 | 1.000 | 1.000 | 10.00 | 100.0 |
| audio_10000 | hnsw | 80 | 0.081 | 0.080 | 1.000 | 1.000 | 10.00 | 100.0 |
| audio_100000 | ivf | 16 | 1.970 | 2.024 | 1.000 | 1.000 | 10.00 | 100.0 |
| audio_100000 | hnsw | 80 | 0.120 | 0.089 | 0.997 | 0.997 | 10.00 | 100.0 |
| imagenes_1000 | ivf | 16 | 0.262 | 0.263 | 0.987 | 0.987 | 10.00 | 100.0 |
| imagenes_1000 | hnsw | 80 | 0.226 | 0.141 | 1.000 | 1.000 | 10.00 | 100.0 |
| imagenes_10000 | ivf | 16 | 0.904 | 0.703 | 0.935 | 0.935 | 10.00 | 100.0 |
| imagenes_10000 | hnsw | 80 | 0.246 | 0.263 | 1.000 | 1.000 | 10.00 | 100.0 |
| imagenes_100000 | ivf | 16 | 2.636 | 2.868 | 0.909 | 0.909 | 10.00 | 100.0 |
| imagenes_100000 | hnsw | 80 | 0.364 | 0.398 | 0.984 | 0.984 | 10.00 | 100.0 |

### Referencia exacta

| Dataset | Indice | Parametro | Euclidiana ms | Coseno ms | Recall E | Recall C | Comunes | % |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| audio_1000 | exacto | 0 | 2.110 | 2.118 | 1.000 | 1.000 | 10.00 | 100.0 |
| audio_10000 | exacto | 0 | 18.791 | 25.021 | 1.000 | 1.000 | 10.00 | 100.0 |
| audio_100000 | exacto | 0 | 222.164 | 240.090 | 1.000 | 1.000 | 10.00 | 100.0 |
| imagenes_1000 | exacto | 0 | 1.829 | 1.960 | 1.000 | 1.000 | 10.00 | 100.0 |
| imagenes_10000 | exacto | 0 | 23.788 | 24.343 | 1.000 | 1.000 | 10.00 | 100.0 |
| imagenes_100000 | exacto | 0 | 249.997 | 276.817 | 1.000 | 1.000 | 10.00 | 100.0 |

### Barrido completo

| Dataset | Indice | Parametro | Euclidiana ms | Coseno ms | Recall E | Recall C | Comunes | % |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| audio_1000 | exacto | 0 | 2.110 | 2.118 | 1.000 | 1.000 | 10.00 | 100.0 |
| audio_1000 | ivf | 1 | 0.030 | 0.027 | 0.921 | 0.921 | 10.00 | 100.0 |
| audio_1000 | ivf | 2 | 0.044 | 0.038 | 0.984 | 0.984 | 10.00 | 100.0 |
| audio_1000 | ivf | 4 | 0.071 | 0.074 | 1.000 | 1.000 | 10.00 | 100.0 |
| audio_1000 | ivf | 8 | 0.126 | 0.149 | 1.000 | 1.000 | 10.00 | 100.0 |
| audio_1000 | ivf | 16 | 0.244 | 0.377 | 1.000 | 1.000 | 10.00 | 100.0 |
| audio_1000 | ivf | 32 | 0.473 | 0.466 | 1.000 | 1.000 | 10.00 | 100.0 |
| audio_1000 | hnsw | 10 | 0.026 | 0.053 | 0.999 | 0.999 | 10.00 | 100.0 |
| audio_1000 | hnsw | 20 | 0.034 | 0.060 | 1.000 | 1.000 | 10.00 | 100.0 |
| audio_1000 | hnsw | 40 | 0.059 | 0.089 | 1.000 | 1.000 | 10.00 | 100.0 |
| audio_1000 | hnsw | 80 | 0.118 | 0.159 | 1.000 | 1.000 | 10.00 | 100.0 |
| audio_1000 | hnsw | 160 | 0.159 | 0.211 | 1.000 | 1.000 | 10.00 | 100.0 |
| audio_1000 | hnsw | 320 | 0.303 | 0.244 | 1.000 | 1.000 | 10.00 | 100.0 |
| audio_10000 | exacto | 0 | 18.791 | 25.021 | 1.000 | 1.000 | 10.00 | 100.0 |
| audio_10000 | ivf | 1 | 0.066 | 0.072 | 0.926 | 0.926 | 10.00 | 100.0 |
| audio_10000 | ivf | 2 | 0.104 | 0.120 | 0.982 | 0.982 | 10.00 | 100.0 |
| audio_10000 | ivf | 4 | 0.174 | 0.181 | 1.000 | 1.000 | 10.00 | 100.0 |
| audio_10000 | ivf | 8 | 0.307 | 0.320 | 1.000 | 1.000 | 10.00 | 100.0 |
| audio_10000 | ivf | 16 | 0.572 | 0.666 | 1.000 | 1.000 | 10.00 | 100.0 |
| audio_10000 | ivf | 32 | 1.111 | 1.341 | 1.000 | 1.000 | 10.00 | 100.0 |
| audio_10000 | ivf | 64 | 2.669 | 2.749 | 1.000 | 1.000 | 10.00 | 100.0 |
| audio_10000 | ivf | 100 | 4.126 | 4.784 | 1.000 | 1.000 | 10.00 | 100.0 |
| audio_10000 | hnsw | 10 | 0.030 | 0.030 | 1.000 | 1.000 | 10.00 | 100.0 |
| audio_10000 | hnsw | 20 | 0.038 | 0.038 | 1.000 | 1.000 | 10.00 | 100.0 |
| audio_10000 | hnsw | 40 | 0.064 | 0.053 | 1.000 | 1.000 | 10.00 | 100.0 |
| audio_10000 | hnsw | 80 | 0.081 | 0.080 | 1.000 | 1.000 | 10.00 | 100.0 |
| audio_10000 | hnsw | 160 | 0.176 | 0.130 | 1.000 | 1.000 | 10.00 | 100.0 |
| audio_10000 | hnsw | 320 | 0.404 | 0.219 | 1.000 | 1.000 | 10.00 | 100.0 |
| audio_100000 | exacto | 0 | 222.164 | 240.090 | 1.000 | 1.000 | 10.00 | 100.0 |
| audio_100000 | ivf | 1 | 0.212 | 0.224 | 0.960 | 0.960 | 10.00 | 100.0 |
| audio_100000 | ivf | 2 | 0.387 | 0.396 | 0.998 | 0.998 | 10.00 | 100.0 |
| audio_100000 | ivf | 4 | 0.623 | 0.652 | 0.999 | 0.999 | 10.00 | 100.0 |
| audio_100000 | ivf | 8 | 1.124 | 1.272 | 1.000 | 1.000 | 10.00 | 100.0 |
| audio_100000 | ivf | 16 | 1.970 | 2.024 | 1.000 | 1.000 | 10.00 | 100.0 |
| audio_100000 | ivf | 32 | 3.625 | 3.709 | 1.000 | 1.000 | 10.00 | 100.0 |
| audio_100000 | ivf | 64 | 6.677 | 7.442 | 1.000 | 1.000 | 10.00 | 100.0 |
| audio_100000 | ivf | 128 | 13.609 | 13.733 | 1.000 | 1.000 | 10.00 | 100.0 |
| audio_100000 | ivf | 316 | 38.668 | 38.871 | 1.000 | 1.000 | 10.00 | 100.0 |
| audio_100000 | hnsw | 10 | 0.034 | 0.042 | 0.995 | 0.995 | 10.00 | 100.0 |
| audio_100000 | hnsw | 20 | 0.045 | 0.050 | 0.997 | 0.997 | 10.00 | 100.0 |
| audio_100000 | hnsw | 40 | 0.064 | 0.059 | 0.997 | 0.997 | 10.00 | 100.0 |
| audio_100000 | hnsw | 80 | 0.120 | 0.089 | 0.997 | 0.997 | 10.00 | 100.0 |
| audio_100000 | hnsw | 160 | 0.198 | 0.165 | 1.000 | 1.000 | 10.00 | 100.0 |
| audio_100000 | hnsw | 320 | 0.257 | 0.284 | 1.000 | 1.000 | 10.00 | 100.0 |
| imagenes_1000 | exacto | 0 | 1.829 | 1.960 | 1.000 | 1.000 | 10.00 | 100.0 |
| imagenes_1000 | ivf | 1 | 0.028 | 0.030 | 0.341 | 0.341 | 9.90 | 99.0 |
| imagenes_1000 | ivf | 2 | 0.047 | 0.050 | 0.544 | 0.544 | 10.00 | 100.0 |
| imagenes_1000 | ivf | 4 | 0.079 | 0.082 | 0.753 | 0.753 | 10.00 | 100.0 |
| imagenes_1000 | ivf | 8 | 0.148 | 0.145 | 0.920 | 0.920 | 10.00 | 100.0 |
| imagenes_1000 | ivf | 16 | 0.262 | 0.263 | 0.987 | 0.987 | 10.00 | 100.0 |
| imagenes_1000 | ivf | 32 | 0.475 | 0.405 | 1.000 | 1.000 | 10.00 | 100.0 |
| imagenes_1000 | hnsw | 10 | 0.069 | 0.048 | 0.966 | 0.966 | 10.00 | 100.0 |
| imagenes_1000 | hnsw | 20 | 0.114 | 0.067 | 0.986 | 0.986 | 10.00 | 100.0 |
| imagenes_1000 | hnsw | 40 | 0.160 | 0.099 | 0.997 | 0.997 | 10.00 | 100.0 |
| imagenes_1000 | hnsw | 80 | 0.226 | 0.141 | 1.000 | 1.000 | 10.00 | 100.0 |
| imagenes_1000 | hnsw | 160 | 0.316 | 0.194 | 1.000 | 1.000 | 10.00 | 100.0 |
| imagenes_1000 | hnsw | 320 | 0.435 | 0.259 | 1.000 | 1.000 | 10.00 | 100.0 |
| imagenes_10000 | exacto | 0 | 23.788 | 24.343 | 1.000 | 1.000 | 10.00 | 100.0 |
| imagenes_10000 | ivf | 1 | 0.063 | 0.064 | 0.352 | 0.352 | 10.00 | 100.0 |
| imagenes_10000 | ivf | 2 | 0.105 | 0.111 | 0.508 | 0.508 | 10.00 | 100.0 |
| imagenes_10000 | ivf | 4 | 0.220 | 0.200 | 0.680 | 0.680 | 10.00 | 100.0 |
| imagenes_10000 | ivf | 8 | 0.354 | 0.397 | 0.809 | 0.809 | 10.00 | 100.0 |
| imagenes_10000 | ivf | 16 | 0.904 | 0.703 | 0.935 | 0.935 | 10.00 | 100.0 |
| imagenes_10000 | ivf | 32 | 1.491 | 1.355 | 0.985 | 0.985 | 10.00 | 100.0 |
| imagenes_10000 | ivf | 64 | 2.743 | 2.542 | 0.999 | 0.999 | 10.00 | 100.0 |
| imagenes_10000 | ivf | 100 | 4.074 | 4.178 | 1.000 | 1.000 | 10.00 | 100.0 |
| imagenes_10000 | hnsw | 10 | 0.062 | 0.070 | 0.924 | 0.924 | 10.00 | 100.0 |
| imagenes_10000 | hnsw | 20 | 0.090 | 0.100 | 0.959 | 0.959 | 10.00 | 100.0 |
| imagenes_10000 | hnsw | 40 | 0.146 | 0.162 | 0.986 | 0.986 | 10.00 | 100.0 |
| imagenes_10000 | hnsw | 80 | 0.246 | 0.263 | 1.000 | 1.000 | 10.00 | 100.0 |
| imagenes_10000 | hnsw | 160 | 0.402 | 0.433 | 1.000 | 1.000 | 10.00 | 100.0 |
| imagenes_10000 | hnsw | 320 | 0.645 | 0.685 | 1.000 | 1.000 | 10.00 | 100.0 |
| imagenes_100000 | exacto | 0 | 249.997 | 276.817 | 1.000 | 1.000 | 10.00 | 100.0 |
| imagenes_100000 | ivf | 1 | 0.219 | 0.231 | 0.258 | 0.258 | 10.00 | 100.0 |
| imagenes_100000 | ivf | 2 | 0.399 | 0.408 | 0.420 | 0.420 | 10.00 | 100.0 |
| imagenes_100000 | ivf | 4 | 0.709 | 0.734 | 0.590 | 0.590 | 10.00 | 100.0 |
| imagenes_100000 | ivf | 8 | 1.375 | 1.445 | 0.766 | 0.766 | 10.00 | 100.0 |
| imagenes_100000 | ivf | 16 | 2.636 | 2.868 | 0.909 | 0.909 | 10.00 | 100.0 |
| imagenes_100000 | ivf | 32 | 5.087 | 5.427 | 0.979 | 0.979 | 10.00 | 100.0 |
| imagenes_100000 | ivf | 64 | 12.149 | 10.352 | 0.993 | 0.993 | 10.00 | 100.0 |
| imagenes_100000 | ivf | 128 | 21.255 | 19.855 | 1.000 | 1.000 | 10.00 | 100.0 |
| imagenes_100000 | ivf | 316 | 40.798 | 42.952 | 1.000 | 1.000 | 10.00 | 100.0 |
| imagenes_100000 | hnsw | 10 | 0.085 | 0.094 | 0.843 | 0.843 | 10.00 | 100.0 |
| imagenes_100000 | hnsw | 20 | 0.121 | 0.134 | 0.895 | 0.895 | 10.00 | 100.0 |
| imagenes_100000 | hnsw | 40 | 0.207 | 0.227 | 0.947 | 0.947 | 10.00 | 100.0 |
| imagenes_100000 | hnsw | 80 | 0.364 | 0.398 | 0.984 | 0.984 | 10.00 | 100.0 |
| imagenes_100000 | hnsw | 160 | 0.669 | 0.701 | 0.994 | 0.994 | 10.00 | 100.0 |
| imagenes_100000 | hnsw | 320 | 1.135 | 1.224 | 0.999 | 0.999 | 10.00 | 100.0 |

## Interpretacion y limites

La auditoria comparo las listas completas de RIDs: el orden fue identico en
las 44 000 parejas de mediciones. Las referencias exactas recuperaron diez
vecinos comunes en todas las consultas. El experimento respalda la equivalencia
esperada sobre estos vectores normalizados; no demuestra igualdad universal
para otros datos ni para vectores sin normalizar.

El unico porcentaje inferior a 100 fue 99 en IVF de 1k imagenes, nprobe=1.
No hubo diferencias entre metricas: los IDs 947, 824, 301 y 598 recuperaron
9, 9, 7 y 5 vecinos respectivamente en ambas. Las listas fueron identicas,
pero la interseccion se divide entre diez. No se rellenaron resultados con
busqueda exacta ni se cambiaron los parametros para ocultar ese caso.

Recall por debajo de uno refleja la aproximacion del indice, no diferencias
entre metricas: tambien coincidio entre ambas. Los centroides IVF fueron
identicos. HNSW se construyo con la metrica correspondiente y los mismos M,
ef_construction y semilla. Se conservaron duplicados y desempates por RID.

Euclidiana tuvo menor latencia exacta resumida en los seis casos. En las
busquedas aproximadas la ventaja no fue uniforme; por ejemplo, HNSW de 1k
imagenes fue mas rapido con coseno y HNSW de 10k imagenes con euclidiana.
No se atribuye toda la diferencia al calculo de distancia: las metricas se
ejecutaron en orden fijo, sin controlar cache ni actividad concurrente.
Son mediciones locales con cinco repeticiones, no una garantia de rendimiento.

Los archivos originales de #190/#191 no se modificaron. Todos los catalogos,
vectores auxiliares y CSV nuevos siguen en carpetas ignoradas. Los CSV y
manifiestos quedan preparados para #193; no se generaron graficas.

## Validacion final al reanudar

Se comprobaron doce ejecuciones completas (seis datasets por dos metricas),
sin etapas pendientes. Se recalcularon las 44 000 comparaciones desde las
88 000 mediciones individuales: Recall estricto, tiempos emparejados,
intersecciones y medianas de medias coinciden con los CSV publicados.
Las consultas, hashes de vectores y centroides IVF son iguales entre metricas.
El mayor RSS de construccion registrado fue 237,9 MiB; el de busqueda, 114,1 MiB.

Pasaron 67 pruebas relacionadas. Durante la revision hubo bloqueos transitorios
de lectura local en Git, Ruff y la prueba que copia el codigo a un temporal.
La ejecucion completa que termino correctamente uso la copia estandar de
Python (`shutil._HAS_FCOPYFILE=False` solo en ese proceso) y tardo 228,77 s.
No se modificaron el motor ni las pruebas existentes para conseguirlo.
Ruff 0.17.3 (`ruff check engine benchmarks`) y `git diff --check` pasaron.
No se repitieron los benchmarks ya completos, ni se hicieron commits o push.
