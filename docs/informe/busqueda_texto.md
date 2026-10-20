# Búsqueda de texto: índice invertido con SPIMI (Parte 3)

Este capítulo cubre la sección 2.3.1 del enunciado: cómo QuipuDB indexa una
columna de texto y responde consultas `MATCH` con TF-IDF y con BM25. La
comparación experimental del 2.3.2 contra el GIN de PostgreSQL está en
[comparacion_texto.md](comparacion_texto.md); las decisiones, con las
mediciones que las respaldan, en el
[ADR 0006](../adr/0006-indice-invertido-y-rankings.md); el detalle de
implementación y el formato en disco, en la sección "Búsqueda de texto" de
[arquitectura.md](../arquitectura.md#busqueda-de-texto-parte-3).

**Responsable:** Juan David Velo Poma (issues #160 a #175). Lo que sigue describe el
código de `main` después de #174.

## Recorrido de una consulta

```text
CREATE INDEX por_texto ON articulos (texto) USING FULLTEXT
        │
        ▼
Heap ──cursor──► TextTokenizer ──► SpimiIndexer ──► bloques ──► InvertedIndex::build
                 (#160)            (#161, 1 MiB)               (#162: mezcla k-way,
                                                                #163–#164: N, df, |d|, normas)

SELECT *, SCORE() AS r FROM articulos WHERE MATCH(texto, 'machine learning') USING BM25
        │
        ▼
parser ─► semántica ─► optimizador: ruta TEXT_SEARCH ─► text_search/inverted_index ─► fetch
                                                        (TfIdfRanking | Bm25Ranking, top-k)
```

## Preprocesamiento del texto

El documento y la consulta pasan por el mismo `TextTokenizer`, con las mismas
opciones, que el índice guarda dentro de su archivo. Las etapas son:

1. separar las corridas de letras o dígitos;
2. pasar a minúsculas;
3. juntar las siglas (`O.N.U.` da `onu`);
4. quitar las stopwords;
5. aplicar el stemmer de Snowball;
6. quitar las tildes al final.

Las listas de stopwords son las de PostgreSQL 17 y los stemmers implementan
Snowball. Se compararon con `ts_lexize` sobre 21 646 palabras en español y
26 263 en inglés, y no hubo **ninguna diferencia**. Así, la comparación contra
el GIN mide estructura y ranking, no dos preprocesamientos distintos.

Las tildes se quitan después del stemmer porque los sufijos de Snowball español
las llevan. Se midió la alternativa de quitarlas antes:

- una consulta escrita sin tildes encontraría el 100 % de las palabras con
  tilde, en vez del 67 %;
- a cambio, agruparía menos variantes de una misma palabra (17 % más términos
  distintos);
- y dejaría de coincidir con `to_tsvector('spanish', ...)`.

Queda como opción (`unaccent_before_stem`).

## Construcción con SPIMI

SPIMI recorre la tabla **una sola vez**. Acumula, en un diccionario local,
cada término con su lista de `(documento, tf)`. Cuando el diccionario llega al
presupuesto, 1 MiB por omisión, ordena los términos y vuelca el bloque a disco.
El presupuesto lo cuenta el propio diccionario, no la memoria del proceso, así
que el mismo corpus siempre da los mismos bloques.

Después, una mezcla k-way (fan-in 64, con niveles si hay más bloques) concatena
las listas de cada término en un solo archivo. Al final del índice escribe las
estadísticas que necesitan los rankings: N, el df de cada término, la longitud
de cada documento, `avgdl` y la norma TF-IDF de cada documento. El archivo sale
**idéntico byte a byte** con uno o con muchos bloques.

Con los 100 000 documentos del 2.3.2:

| Qué | Valor |
|---|---|
| Bloques | 17 |
| Pico del diccionario | 1 024 KiB, igual al presupuesto |
| Pasadas de mezcla | 1 |
| Tiempo de construcción | 1,26 s, con la tokenización incluida |
| Índice | 7,7 MiB, frente a 26 MiB de la tabla |

## Rankings

| | TF-IDF + coseno | BM25 |
|---|---|---|
| Peso del término | `(1 + log10 tf) · log10(N / df)` | `idf · tf (k1 + 1) / (tf + k1 (1 − b + b·|d|/avgdl))` |
| idf | `log10(N / df)` | `ln(1 + (N − df + 0,5) / (df + 0,5))`, la variante de Lucene, siempre positiva |
| Normalización | Coseno contra la norma del documento, precalculada | Por longitud contra `avgdl` (`b = 0,75`); saturación del tf (`k1 = 1,2`) |
| En SQL | `USING TF_IDF` | `USING BM25`, y el ranking por omisión |

Los dos recorren solo las listas de los términos de la consulta y se quedan con
el top-k en un heap. Los dos reproducen, con pruebas, sus ejemplos de las
diapositivas y un cálculo por fuerza bruta sobre 400 documentos.

## SQL

```sql
CREATE INDEX por_texto ON articulos (texto) USING FULLTEXT;   -- o INVERTED_INDEX

SELECT * FROM articulos WHERE MATCH(texto, 'base de datos') USING TF_IDF LIMIT 10;

SELECT *, SCORE() AS relevancia FROM articulos
WHERE MATCH(texto, 'machine learning') USING BM25
ORDER BY relevancia DESC;
```

- `MATCH` exige un índice de texto sobre la columna: sin él no hay df ni N
  para rankear.
- Las filas salen por relevancia, y `LIMIT k` baja al índice como top-k.
- `ORDER BY relevancia DESC` no usa External Sorting: es el orden en que el
  índice entrega los documentos.
- El plan muestra `text_search/inverted_index`, con el ranking, seguido de
  `fetch`.
- Un `INSERT` o un `DELETE` dejan el índice desactualizado, y la siguiente
  búsqueda lo reconstruye. Se reconstruye entero, porque un documento nuevo
  cambia N, avgdl y el df de sus términos.

La gramática completa está en el [ADR 0003](../adr/0003-procesamiento-consultas-sql.md).

## Resultados experimentales (2.3.2)

Sobre 1 000, 10 000 y 100 000 preguntas de BEIR Quora, con 300 consultas de 1,
3 y 5+ palabras y juicios de relevancia hechos por personas (#171):

- **Costo.** TF-IDF y BM25 cuestan lo mismo en orden de magnitud: leen las
  mismas páginas, y el tiempo crece con los documentos que coinciden con la
  consulta (#172).
- **Relevancia.** Con 3 y 5+ palabras, TF-IDF y BM25 no se distinguen. Con una
  palabra, TF-IDF es mejor en 10k y 100k, porque BM25 premia repetir el término
  (#173).
- **Contra el GIN de PostgreSQL**, con la misma semántica (OR), QuipuDB
  responde de 2 a 4 veces más rápido. El GIN no guarda frecuencias y visita la
  tabla para calcular `ts_rank`. Además, `ts_rank`, sin idf, rankea peor. El
  GIN construye 1,3 veces más rápido y su índice es más chico, aunque necesita
  la columna `tsvector` para rankear (#174).

Las cifras, gráficas y la tabla de ventajas y desventajas están en
[comparacion_texto.md](comparacion_texto.md).

## Límites

- No hay búsqueda de frases ni de prefijos: los postings no guardan
  posiciones.
- Sin terminación temprana (WAND, MaxScore), una consulta puntúa todos los
  documentos que coinciden antes de quedarse con k.
- El índice se reconstruye entero después de cada cambio en la tabla: conviene
  para corpus que se cargan y se consultan, no para escrituras continuas.
- Un índice tiene un solo idioma; desde SQL se crea en español, y otro idioma
  se elige desde los bindings (`TextIndex.build(opciones)`).
- El texto vive en una columna `VARCHAR` de largo fijo: un documento largo pide
  páginas más grandes.

## Evidencia

- Código: [tokenizador](../../core/src/index/text_tokenizer.cpp),
  [SPIMI](../../core/src/index/spimi.cpp),
  [índice invertido](../../core/src/index/inverted_index.cpp),
  [TF-IDF](../../core/src/index/tfidf_ranking.cpp),
  [BM25](../../core/src/index/bm25_ranking.cpp),
  [índice en el motor](../../core/src/index/text_index.cpp).
- Pruebas del core en `core/tests/index/`: `text_tokenizer_test`,
  `text_stemmer_test`, `spimi_bloques_test`, `inverted_index_test`,
  `texto_estadisticas_test`, `tfidf_ranking_test`, `bm25_ranking_test` y
  `text_index_test`.
- Pruebas del motor: `engine/test_text_index.py`, `engine/test_query_processor.py`
  y las del parser y el planner sobre `MATCH` y `SCORE()`.
- Mediciones: [benchmarks/README.md](../../benchmarks/README.md), secciones
  #171 a #174.
