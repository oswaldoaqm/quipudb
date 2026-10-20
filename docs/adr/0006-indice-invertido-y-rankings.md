# ADR 0006 - Indice invertido con SPIMI y rankings TF-IDF y BM25

- **Fecha:** 2026-10-19
- **Estado:** Aceptada
- **Issues:** #160 a #170 (implementacion), #171 a #174 (mediciones), #175 (este ADR)

## Contexto

La seccion 2.3.1 del enunciado pide busqueda de texto sobre una columna:
un indice invertido construido con SPIMI, que funcione con memoria acotada, y
consultas `MATCH` que devuelvan los documentos mas relevantes con TF-IDF y con
BM25. La 2.3.2 pide compararlo contra el GIN de PostgreSQL.

Este ADR reune las decisiones que se tomaron en los issues de la Parte 3, con
las mediciones que respaldan cada una. El detalle de implementacion y el
formato byte a byte estan en `docs/arquitectura.md` (seccion "Busqueda de texto
(Parte 3)"). Los resultados experimentales estan en
`docs/informe/comparacion_texto.md`.

## Decision

### 1. Tokenizacion compatible con `to_tsvector` de PostgreSQL

Todo texto, el que se indexa y el que se busca, pasa por `TextTokenizer`, en
este orden:

1. separar en corridas de letras o digitos;
2. pasar a minusculas;
3. juntar las siglas (`O.N.U.` da `onu`);
4. quitar las stopwords;
5. aplicar el stemmer de Snowball (no a las palabras con digitos);
6. quitar las tildes, conservando la `ñ`.

| Decision | Por que |
|---|---|
| Stopwords: las listas de Snowball que distribuye PostgreSQL 17 (`spanish.stop`, 313 palabras; `english.stop`, 127) | El GIN de la comparacion del 2.3.2 descarta exactamente las mismas palabras: se compara la estructura y el ranking, no dos preprocesamientos |
| Stemmers: Snowball espanol e ingles, implementados en C++ | Son los de `to_tsvector('spanish' \| 'english', ...)` |
| Espanol por omision; el idioma, las stopwords extra y el stemmer son `TokenizerOptions` | El dominio del proyecto esta en espanol; el corpus del 2.3.2 (Quora) esta en ingles y se indexa con `build(opciones)` |
| Las opciones se guardan **dentro del indice** | La consulta se tokeniza con las mismas opciones que los documentos. Si no, la palabra esta en el documento y la busqueda no la encuentra |
| Terminos de mas de 64 bytes se descartan, no se truncan | La entrada del diccionario vive en una pagina de 4 KiB; truncar haria coincidir palabras distintas con el mismo prefijo |
| Las tildes se quitan **despues** del stemmer (por omision) | Ver la medicion de abajo |

**Mediciones.**

- **Stemmers.** Cada stemmer se comparo con `ts_lexize` de PostgreSQL 17 sobre
  un vocabulario grande, y coincidieron en todas las palabras (#160):

  | Stemmer | Palabras | Distintas |
  |---|---:|---:|
  | espanol | 21 646 | 0 |
  | ingles | 26 263 | 0 |

- **Tokenizacion completa.** Contra `to_tsvector('english', ...)` sobre los
  100 000 documentos del 2.3.2, con OR, las consultas de 5+ palabras
  encuentran en promedio 4 114 documentos en QuipuDB y 4 102 en PostgreSQL
  (#174). La diferencia viene de casos que el parser de PostgreSQL trata
  distinto, como guiones y URLs.

**Tildes antes o despues del stemmer** (la decision que #160 dejo para este
ADR). Snowball espanol necesita las tildes: sus sufijos las llevan (`-ación`),
y sin ellas `información` no se reduce. La opcion `unaccent_before_stem` quita
las tildes antes, que es la receta de PostgreSQL con la extension `unaccent`
delante del stemmer. Se midio con el vocabulario espanol de la validacion del
stemmer: 21 773 palabras, 6 298 con tilde.

| Opcion | La consulta sin tilde encuentra la palabra con tilde | Terminos distintos |
|---|---:|---:|
| Tildes despues del stemmer (**elegida**) | 4 227 de 6 298 (67,1 %) | 5 463 |
| Tildes antes del stemmer | 6 298 de 6 298 (100 %) | 6 383 |

Quitarlas antes hace que escribir sin tildes funcione siempre, pero agrupa
menos variantes de una misma palabra (17 % mas terminos distintos). Ademas
dejaria de coincidir con `to_tsvector('spanish', ...)`, contra el que se valido
el stemmer y se compara el GIN. Se mantiene el orden de PostgreSQL, y la otra
queda como opcion para un corpus donde las consultas lleguen sin tildes. El
vocabulario incluye formas sinteticas con cada sufijo, que eran necesarias para
validar el stemmer: los porcentajes describen las reglas de sufijos, no la
frecuencia de esas palabras en un texto real. El corpus del 2.3.2 esta en
ingles, asi que no aporta datos sobre esta decision.

### 2. SPIMI con un presupuesto de memoria contado

| Decision | Por que |
|---|---|
| Un diccionario **local** por bloque, sin termID global | Es lo que distingue a SPIMI de BSBI: la memoria la acota el bloque, no el vocabulario |
| Presupuesto de **1 MiB** por omision, contado por el propio diccionario: `72 + bytes del termino + 4 postings` por termino nuevo, y lo que ya tenia cada vez que una lista se duplica | La RAM del proceso depende del asignador y no se repite entre corridas: las pruebas no podrian fijar cuantos bloques salen. Las listas se cuentan exactas; los 72 bytes son una estimacion fija del nodo del hash, el `std::string` y el `std::vector` |
| El bloque se vuelca **antes** de agregar un posting que no cabe | Ningun bloque pasa del presupuesto (`peak_memory <= memory_budget`) |
| Documentos en orden estrictamente creciente | Las listas salen ordenadas por documento sin ordenar nada, y mezclar bloques es concatenar listas |
| Las longitudes de los documentos se escriben de corrido a un archivo, no se acumulan | Con 100 000 documentos serian una lista que crece sin limite, por fuera del presupuesto |

**Medicion (#172).** Con 100 000 documentos, el diccionario llega al tope de
1 024 KiB, se vuelcan 17 bloques y alcanza una sola pasada de mezcla. Lo que
crece con N es la cantidad de bloques, no la memoria:

| N | Bloques | Pico del diccionario |
|---:|---:|---:|
| 1 000 | 1 | 278 KiB |
| 10 000 | 2 | 1 024 KiB |
| 100 000 | 17 | 1 024 KiB |

`TextIndex.last_build` expone estas cifras.

### 3. Mezcla k-way y un solo archivo

| Decision | Por que |
|---|---|
| Mezcla con cola de prioridad y una pagina por bloque abierto, fan-in 64 | Con mas bloques se mezcla por niveles, como el External Sorting: `1 + niveles` pasadas |
| Un solo archivo con varios flujos de paginas encadenadas | Las listas de postings cruzan paginas libremente; el diccionario no, para que buscar un termino lea **una** pagina del diccionario. Un indice disperso con el primer termino de cada pagina del diccionario se carga al abrir |
| El resultado no depende de cuantos bloques hubo | El mismo corpus da el mismo archivo **byte por byte** con 1 bloque, con muchos y con cualquier fan-in. Las pruebas lo comparan, y es la prueba que mas errores del merge detecta |
| El formato tiene version (hoy la 3) | Abrir un indice de otra version es un `IoError` claro, no basura |

### 4. Las estadisticas del ranking viven en el indice

`N`, el df de cada termino, la longitud de cada documento, `avgdl` y la norma
de cada documento con las dos variantes de tf. Las normas se calculan en la
pasada final del merge, el unico momento en que se conocen el df y el N
definitivos. Calcularlas en la consulta seria recorrer el indice entero.

**Consecuencia medida (#174):** QuipuDB rankea **sin salir del indice**. El GIN
de PostgreSQL no guarda frecuencias, y para calcular `ts_rank` visita la tabla
por cada candidato. Con 100 000 documentos y consultas de 5+ palabras, QuipuDB
lee 71 KiB por consulta y PostgreSQL 12 736 KiB, y BM25 responde 4,3 veces mas
rapido que el GIN con la misma semantica (OR).

### 5. Las formulas

TF-IDF con similitud del coseno (#164):

```
cos(q, d) = sum_t w(t,q) w(t,d) / (||q|| ||d||)
w(t,x)    = tf_w(tf_{t,x}) * log10(N / df_t)
tf_w(tf)  = 1 + log10(tf)            (kLog, por omision)  |  tf  (kRaw)
```

BM25 (#165), con la variante de Lucene del idf:

```
score(q,d) = sum_t qtf * idf(t) * tf(t,d) (k1 + 1) / (tf(t,d) + k1 (1 - b + b |d| / avgdl))
idf(t)     = ln(1 + (N - df_t + 0.5) / (df_t + 0.5))          k1 = 1.2, b = 0.75
```

| Decision | Por que |
|---|---|
| TF-IDF: idf sin suavizado, tf logaritmico por omision | Es el de las diapositivas; con `kRaw` se reproduce exactamente su ejemplo resuelto. Un termino que esta en todos los documentos tiene idf 0 y su lista ni se lee |
| BM25: idf de Lucene y no el de Robertson y Sparck Jones | El clasico es negativo cuando el termino esta en mas de la mitad de los documentos: tenerlo restaria puntos. La variante de Lucene es siempre positiva y casi igual para terminos raros; la usan Elasticsearch y MongoDB |
| `k1 = 1.2`, `b = 0.75` | Los valores de Lucene y de la literatura. Son parametros de `Bm25Ranking`, no constantes |
| Las dos implementan `TextRanking` | El parser y el benchmark las intercambian sin ramificar |
| Desempate por documento creciente; top-k con un heap de k | El resultado es determinista; `LIMIT k` no ordena todos los coincidentes |
| Sin `USING`, `MATCH` usa **BM25** | Ver abajo |

**Por que BM25 por omision, con los datos de #173.** Con consultas de 3 y 5+
palabras, TF-IDF y BM25 no se distinguen en relevancia: devuelven casi el mismo
top-10, y ninguna diferencia de Recall@10 pasa la prueba pareada. En tiempo,
con 100k y esas consultas BM25 fue mas rapido (0,31 frente a 0,49 ms con 3
palabras; 0,73 frente a 1,40 ms con 5+), aunque los casos de TF-IDF tuvieron
una dispersion alta (#172).
Con una sola palabra, TF-IDF es mejor en 10k y 100k (Recall@10 +0,099 con 100k,
p < 0,001). La causa: BM25 premia que el documento repita la palabra, y en
Quora eso son preguntas comparativas, no duplicados. Es propio de un corpus de
preguntas de una linea. En documentos largos, que la palabra se repita indica
de que trata el documento, y ahi la saturacion del tf y la normalizacion por
longitud de BM25 son su ventaja. Se mantiene BM25 por omision, que es el
estandar, y `USING TF_IDF` queda a una palabra de distancia.

### 6. El indice en el motor

| Decision | Por que |
|---|---|
| El texto vive en una columna `VARCHAR` de un Heap | Es un dato mas de la tabla, y el RID del Heap es estable |
| Documentos densos 1..N, con el mapeo a RID en `<indice>.rids` | El indice invertido usa ids de 32 bits; un RID empaquetado limitaria la tabla a 65 535 paginas |
| No se actualiza fila por fila: `INSERT`/`DELETE` lo marcan **desactualizado** (`<indice>.desactualizado`) y la siguiente busqueda lo reconstruye | Un documento nuevo cambia N, avgdl y el df de sus terminos, y con eso el puntaje de documentos que no se tocaron. Cargar 10 000 filas cuesta una reconstruccion, no 10 000. La marca es un archivo para que sobreviva al proceso |
| `build()` construye al lado y reemplaza al final | Si falla a medias, el indice anterior sigue sirviendo |

### 7. SQL

```sql
CREATE INDEX por_texto ON articulos (texto) USING FULLTEXT;      -- o INVERTED_INDEX
SELECT *, SCORE() AS relevancia FROM articulos
WHERE MATCH(texto, 'machine learning') USING BM25                -- o TF_IDF; sin USING, BM25
ORDER BY relevancia DESC LIMIT 10;
```

| Decision | Por que |
|---|---|
| `USING FULLTEXT` o `USING INVERTED_INDEX` | El enunciado no dice como se crea el indice. `FULLTEXT` es la palabra de MySQL; `INVERTED_INDEX`, el nombre de la estructura, como `EXTENDIBLE_HASH` para `HASH` |
| `MATCH` es una condicion de `WHERE` con su ruta (`text_search` seguido de `fetch`) | El mismo camino que `DISTANCIA` y `DENTRO`; sin indice de texto es un error, porque sin df, N ni avgdl no hay ranking |
| `LIMIT k` baja al indice como top-k | Sin `ORDER BY` ni `GROUP BY`, el `LIMIT` es el top-k del ranking |
| `ORDER BY relevancia DESC` no usa External Sorting | El indice ya entrega ese orden; `ASC` es el inverso |
| El plan declara el ranking en el campo `ranking` del paso `text_search` (#170) | Tambien cuando se uso el de por omision; ver el ADR 0002 |

La gramatica esta en el ADR 0003.

## Alternativas descartadas

| Alternativa | Por que no |
|---|---|
| BSBI con un diccionario global termino -> termID | La memoria creceria con el vocabulario, que es justo lo que SPIMI evita |
| Medir la memoria real del proceso para decidir cuando volcar | No es determinista: el mismo corpus daria bloques distintos en cada corrida y en cada maquina |
| Calcular la norma de TF-IDF al consultar | Habria que recorrer todos los terminos de cada documento candidato, es decir, el indice entero |
| Actualizar el indice fila por fila | Cambia el puntaje de documentos que no se tocaron; seria reconstruirlo igual, pero en cada `INSERT` |
| Guardar posiciones en los postings | Permitirian frases (`"base de datos"`), que el enunciado no pide; duplicarian el tamano de las listas |
| Quitar las tildes antes del stemmer por omision | Ver la medicion de la seccion 1 |

## Consecuencias

- El indice se construye con memoria acotada sin importar el tamano del corpus;
  un corpus mas grande solo agrega bloques y, pasado el fan-in, niveles de
  mezcla.
- El costo de una consulta crece con la suma de las listas de sus terminos: no
  hay terminacion temprana (WAND, MaxScore). Con 100k, una consulta de 5+
  palabras puntua unos 4 100 documentos para quedarse con 10.
- Rankear sin salir del indice le cuesta espacio: 7,7 MiB con 100k documentos,
  frente a 4,3 MiB del GIN (que necesita, aparte, 7,8 MiB de `tsvector` en la
  tabla para rankear).
- Una tabla que recibe escrituras todo el tiempo reconstruye el indice en cada
  busqueda que sigue a una escritura: 1,26 s con 100k. Para ese caso conviene
  el GIN.
- Un indice tiene un idioma. Un corpus mezclado puede pedir las stopwords de
  los dos idiomas, sabiendo que la lista inglesa descarta palabras que en
  espanol significan algo (`once`, `don`).
