# TF-IDF + coseno vs BM25 — issue #172

## Estado y trazabilidad

**Corrida oficial completada: 105 mediciones, 21 resumenes y un CSV de entorno.**
Suite `texto`, 7 casos por tamano (una construccion y seis lotes de consultas),
un calentamiento y cinco repeticiones medidas, 100 consultas por lote. Todas las
repeticiones pasaron la validacion: top-10 ordenados, y respuestas y cuenta de
SPIMI identicas entre repeticiones. No se descartaron outliers ni se repitieron
casos por separado.

ID: `20261019T225528Z_c576d5c784fa40a8a9ae8b0b1276895a`.
Inicio: 2026-10-19T22:55:28Z (19 de octubre, 17:55:28, America/Lima).
Commit medido: `0d9cfab`. `git_cambios=True` se debe solo a cambios locales en
`.gitignore` y en `engine/api/service.py`, que no estan en la ruta medida.

- [Mediciones](results/20261019T225528Z_c576d5c784fa40a8a9ae8b0b1276895a_mediciones.csv)
- [Resumen](results/20261019T225528Z_c576d5c784fa40a8a9ae8b0b1276895a_resumen.csv)
- [Entorno](results/20261019T225528Z_c576d5c784fa40a8a9ae8b0b1276895a_entorno.csv)

Los CSV estan ignorados por Git, como los de #39, #40 y #131: hay que
conservarlos aparte. Las tablas de Resultados las genera
`python benchmarks/scripts/resumir_texto.py 20261019T225528Z_c576d5c784fa40a8a9ae8b0b1276895a`,
sin copiar numeros a mano.

Datasets de #171 (BEIR Quora), con su SHA-256:

| N | `documentos_N.csv` |
|---:|---|
| 1 000 | `63e0d9ccdf8d35065dc0f8ea909b9831b804103492fca9b1856dd239928ca88f` |
| 10 000 | `22957c223fe4a4e53482fd0d1c57bbc17e3b03914a1ebc80db4de77c8c4fd75c` |
| 100 000 | `ec705b99aca12f8d3cfdd749f5288fdc025eb895b9c86526336b54c787e52b61` |

### Una corrida previa descartada, y por que

La primera corrida completa (`20261019T224427Z_a52461ef9cc44a6db274cd03c1c4ccf1`)
paso todas las validaciones. Solo tenia la afinidad a la CPU 2 y la prioridad
alta, sin tocar el plan de energia, y sus tiempos salieron **bimodales**, igual
que en la primera corrida de #131. Por ejemplo, `bm25_consulta_1_palabras` en
100k dio 0.149 y 0.137 ms en las dos primeras repeticiones y 0.083–0.086 ms en
las otras tres. Se descarto entera. La oficial repite las condiciones de #131:
proceso fijado a la CPU logica 2 con prioridad alta, y estado minimo del
procesador al 100 % con corriente alterna (restaurado al 5 % al terminar). Las
dos quedan en el entorno como `declarado.afinidad` y `declarado.energia`.

Aun asi, la dispersion no desaparece en todos los casos. La seccion
"TF-IDF frente a BM25" la lee caso por caso.

## Entorno

- Intel Core i5-1335U (13.a gen., 2 nucleos P y 8 E, 12 CPU logicas), 16 GB de
  RAM, SSD NVMe. Es la misma maquina de #131.
- Windows 11 Pro 10.0.26200, con corriente alterna.
- CPython 3.11.9 de MSYS2 ucrt64; core y bindings en Release con g++ 14.1.0.
- Temporales en `%TEMP%`, el mismo SSD para todos los casos.

La cache del sistema operativo no se controla: los archivos recien escritos
estan, en la practica, en memoria. Los tiempos miden CPU y acceso a paginas en
cache, no lecturas fisicas del SSD.

## Protocolo

Resumido; el detalle esta en el [README](README.md#tf-idf--coseno-vs-bm25-issue-172).

- Corpus de #171: 1k, 10k y 100k preguntas de Quora en ingles, con las
  stopwords y el stemmer ingleses de Snowball. Son textos cortos, de 53 bytes
  de mediana.
- 300 consultas: 100 preguntas base, y de cada una una consulta de 1 palabra,
  una de 3 y una de 5+ (4,76 terminos en promedio despues de las stopwords).
  Son las mismas en los tres tamanos.
- **Una construccion para las dos tecnicas.** El reloj mide `TextIndex.build`:
  SPIMI, la mezcla de los bloques y el archivo de RIDs, mas un flush. El Heap se
  carga fuera del reloj.
- **Consultas:** `search_text(consulta, ranking, 10)` para cada una de las 100
  consultas de una longitud. El tiempo por consulta es la mediana de las cinco
  repeticiones del lote dividida por 100. Incluye el cruce Python/binding.
  Devuelve RID sin leer los registros.
- **Memoria:** la cuenta determinista de SPIMI (`TextIndex.last_build`), no la
  RAM del proceso. El README explica por que.

## Resultados

<!-- generado por resumir_texto.py desde 20261019T225528Z_c576d5c784fa40a8a9ae8b0b1276895a -->

#### Construccion y espacio (comunes a TF-IDF y BM25)

| N | Construccion ms | Dispersion | Pag. leidas | Pag. escritas | Terminos | Heap KiB | Indice KiB | Indice / Heap |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 000 | 45.6 | 24 % | 87 | 50 | 2 425 | 272 | 136 | 0.50 |
| 10 000 | 106.9 | 6 % | 835 | 380 | 9 400 | 2 672 | 916 | 0.34 |
| 100 000 | 1 257.6 | 33 % | 8 307 | 3 455 | 29 367 | 26 672 | 7 856 | 0.29 |

#### Memoria de la construccion

| N | Bloques SPIMI | Pasadas de mezcla | Pico del diccionario KiB | Presupuesto KiB | Lectores de la mezcla KiB | RIDs KiB |
|---:|---:|---:|---:|---:|---:|---:|
| 1 000 | 1 | 1 | 277.6 | 1 024 | 4 | 8 |
| 10 000 | 2 | 1 | 1 024.0 | 1 024 | 8 | 78 |
| 100 000 | 17 | 1 | 1 024.0 | 1 024 | 68 | 781 |

#### Consultas, top-10

| Palabras | N | TF-IDF ms | BM25 ms | BM25 / TF-IDF | Dispersion TF-IDF / BM25 | Pag./consulta | Terminos | Coincidentes |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 1 000 | 0.017 | 0.021 | 1.29 | 24 % / 44 % | 1.9 | 1.00 | 3 |
| 1 | 10 000 | 0.023 | 0.065 | 2.88 | 74 % / 37 % | 2.0 | 1.00 | 22 |
| 1 | 100 000 | 0.066 | 0.083 | 1.26 | 24 % / 28 % | 2.4 | 1.00 | 202 |
| 3 | 1 000 | 0.037 | 0.037 | 1.00 | 56 % / 56 % | 5.9 | 3.00 | 19 |
| 3 | 10 000 | 0.139 | 0.182 | 1.31 | 20 % / 70 % | 6.3 | 3.00 | 165 |
| 3 | 100 000 | 0.491 | 0.312 | 0.64 | 66 % / 26 % | 9.3 | 3.00 | 1 619 |
| 5+ | 1 000 | 0.056 | 0.055 | 0.98 | 8 % / 15 % | 9.1 | 4.76 | 45 |
| 5+ | 10 000 | 0.223 | 0.188 | 0.84 | 53 % / 51 % | 10.0 | 4.76 | 417 |
| 5+ | 100 000 | 1.403 | 0.730 | 0.52 | 77 % / 5 % | 17.7 | 4.76 | 4 114 |

`Dispersion` es (max - min) / mediana del tiempo de las cinco repeticiones.
`Coincidentes` son los documentos que tienen algun termino de la consulta: el
ranking los puntua a todos antes de quedarse con 10.

## Lectura de los resultados

### La construccion es lineal y la memoria no crece con N

De 10k a 100k documentos, la construccion pasa de 107 ms a 1,26 s (x11,8) y las
paginas leidas y escritas crecen x10 y x9. SPIMI recorre la tabla una vez (las
paginas leidas son las del Heap mas la relectura de los bloques en la mezcla) y
escribe cada posting dos veces: en su bloque y en el indice final. Con 1k, la
construccion tarda 46 ms y la domina crear y borrar archivos, que en Windows
cuesta casi lo mismo para cualquier tamano.

La memoria es lo que distingue a SPIMI. El diccionario del bloque en
construccion **nunca pasa de su presupuesto de 1 MiB**: con 10k y con 100k llega
al tope (1 024,0 KiB) y ahi se vuelca. Lo que crece con N es la **cantidad de
bloques** (1, 2 y 17), no la memoria. Con 17 bloques alcanza una sola pasada de
mezcla (fan-in 64), que tiene abierta una pagina por bloque: 68 KiB. Lo unico
proporcional a N es el mapeo de documentos a RID, 8 bytes por documento
(781 KiB con 100k). La construccion de 100k trabaja con unos 1,9 MiB para
producir un indice de 7,7 MiB a partir de una tabla de 26 MiB. Con un corpus 10
veces mayor, el diccionario seguiria en 1 MiB y solo habria mas bloques.

### Espacio: el indice ocupa menos de un tercio de la tabla

El indice (archivo mas RIDs) mide 0,50, 0,34 y 0,29 veces el Heap. La fraccion
baja porque el vocabulario crece mucho mas despacio que el corpus (2 425, 9 400
y 29 367 terminos, la ley de Heaps), mientras las listas de postings crecen con
N. El Heap esta inflado por el `VARCHAR(256)` de largo fijo: con textos de 53
bytes de mediana, un formato de largo variable lo achicaria, pero eso es del
storage, no del indice.

El indice es **uno solo**: TF-IDF y BM25 ocupan exactamente el mismo espacio,
porque por documento se guardan la norma (para el coseno) y la longitud (para
BM25) en el mismo registro de 24 bytes.

### Las consultas cuestan segun las listas que recorren

Las dos tecnicas leen **las mismas paginas** en todos los casos: recorren las
mismas listas de postings y la misma estadistica por documento. Son pocas, de 2
a 18 por consulta aunque haya miles de documentos coincidentes, porque cada
lista esta contigua en el archivo.

El tiempo crece con los documentos coincidentes, no con N directamente:

- 1 palabra: de 3 a 202 coincidentes entre 1k y 100k, y de 0,02 a 0,07–0,08 ms;
- 5+ palabras: de 45 a 4 114 coincidentes, y de 0,06 a 0,7–1,4 ms.

Cada termino suma su lista entera, y los terminos comunes de una pregunta
(`best`, `way`, `learn`) traen listas largas. Por eso, con 100k, una consulta
de 5+ palabras cuesta entre 9 (BM25) y 21 (TF-IDF) veces mas que una de una. Ningun ranking tiene poda:
los dos puntuan todos los coincidentes y recien al final se quedan con el
top-10 (`keep_top_k`).

### TF-IDF frente a BM25: el mismo orden de costo

| Caso | Mediana BM25 / TF-IDF | Minimo BM25 / minimo TF-IDF |
|---|---:|---:|
| 1 palabra, 100k | 1,26 | 1,10 |
| 3 palabras, 100k | 0,64 | 0,81 |
| 5+ palabras, 100k | 0,52 | 0,72 |

Los datos admiten dos lecturas, y no alcanzan para elegir del todo entre ellas:

- **Con 1k y 10k no hay ganador.** Los cocientes van de 0,84 a 2,88, en los dos
  sentidos, con dispersiones de hasta 74 %. Son tiempos de decimas de
  milisegundo, donde pesa mas el cruce Python/binding que el ranking.
- **Con 100k y consultas de 3 o 5+ palabras, BM25 sale mas barato**, en mediana
  y en minimo. Pero los casos de TF-IDF son justo los mas dispersos (66 % y
  77 %). Una verificacion manual dio una diferencia menor: sin el ajuste de
  energia, alterno los dos rankings sobre el mismo indice de 100k (12 lotes de
  cada uno) y midio 1,04 frente a 0,90 ms con 3 palabras y 2,30 frente a
  2,01 ms con 5+ (BM25 entre 13 % y 14 % mas rapido).

Lo que si sostienen los datos es que **elegir el ranking no cambia el orden de
magnitud del costo**. Los dos leen las mismas paginas y recorren los mismos
postings. La diferencia, si existe, viene de la aritmetica por posting y no
llega a un factor de 2. Una explicacion plausible, que no se midio aislada:
TF-IDF calcula un `log10` por posting y despues busca la norma de cada
candidato en el orden de la tabla hash; BM25 hace una division por posting y
lee la longitud de cada documento en el orden de la lista. Como el costo es
comparable, la eleccion entre los dos se decide por la **calidad del ranking**,
que es lo que mide #173.

## Limites

- El corpus es de textos cortos (preguntas). Con documentos largos cambian las
  listas y las longitudes, y la normalizacion por longitud de BM25 pesa mas.
- La memoria es la cuenta de SPIMI, no la del proceso. Por encima de esa
  cuenta, el proceso tiene el interprete, el cursor de la tabla y el asignador.
- Las paginas son las del contador del core. Las lecturas reales del disco no
  se miden, porque la cache del sistema no se controla.
- Ninguno de los dos rankings tiene terminacion temprana (WAND, MaxScore). Con
  ella el costo dejaria de crecer con todos los coincidentes, y sacaria ventaja
  el ranking con cotas mas ajustadas.
