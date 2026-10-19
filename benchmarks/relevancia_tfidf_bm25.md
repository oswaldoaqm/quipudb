# Relevancia de TF-IDF + coseno y BM25 — issue #173

## Estado y trazabilidad

**Precision@10 y Recall@10 de TF-IDF y BM25 sobre los tres corpus de #171, por
longitud de consulta.** 300 consultas (100 por longitud) por tecnica y tamano,
contra los juicios de BEIR Quora. El GIN de PostgreSQL entra en #174 con el
mismo formato de rankings y el mismo calculo (`relevancia_texto.py`).

Rankings: `20261019T232944Z_34775e724d824378964e9b9b674d00ad`, commit
`f620d82` (`git_cambios=True` solo por cambios locales en `.gitignore` y
`engine/api/service.py`, fuera de la ruta medida). Los rankings son
deterministas: dos corridas dieron el mismo archivo, SHA-256
`cd9808b7d82b656f8fa24ed79c9d69e020c3e891da61d77bf21d8491e5f689e2`.

- [Rankings](results/20261019T232944Z_34775e724d824378964e9b9b674d00ad_rankings.csv)
- [Entorno](results/20261019T232944Z_34775e724d824378964e9b9b674d00ad_entorno.csv)
- [Relevancia por consulta](results/20261019T232944Z_34775e724d824378964e9b9b674d00ad_relevancia_por_consulta.csv),
  [resumen](results/20261019T232944Z_34775e724d824378964e9b9b674d00ad_relevancia_resumen.csv)
  y [comparacion pareada](results/20261019T232944Z_34775e724d824378964e9b9b674d00ad_relevancia_pareada.csv)

Los CSV estan ignorados por Git, como los de #172. Se regeneran con:

```bash
PYTHONPATH=build-py/bindings python -B benchmarks/scripts/rankings_texto.py
python -B benchmarks/scripts/relevancia_texto.py benchmarks/results/<id>_rankings.csv
```

## Que se mide

Por consulta, con R los relevantes y T los 10 primeros del ranking:

- **Precision@10** = |T n R| / 10. Si la tecnica devuelve menos de 10, los que
  faltan cuentan como no relevantes, como en trec_eval.
- **Recall@10** = |T n R| / |R|.

Cada una se promedia sobre las 100 consultas de cada longitud (macro promedio).
El codigo no sabe de donde sale cada ranking: las tres tecnicas pasan por las
mismas funciones, probadas con casos calculados a mano
(`benchmarks/test_relevancia_texto.py`).

Hay tres datos mas para leer las metricas sin malinterpretarlas:

- **Techo de P@10.** En Quora cada consulta tiene en promedio 1,61 relevantes
  (#171), asi que ni un ranking perfecto pasa de **0,161**. Una P@10 de 0,15 es
  casi perfecta, no mala. Para Quora, Recall@10 es la metrica que mas informa.
- **Empates en el corte.** Si el 10.o empata en puntaje con el 11.o, el que
  entra lo decide el desempate (en QuipuDB, el id menor) y no el ranking. Por
  eso el ranking guarda todos los empatados con el 10.o y se informa tambien la
  version con **empates promediados** (McSherry y Najork, ECIR 2008): el valor
  esperado sobre todos los ordenes posibles dentro del grupo empatado.
- **Prueba pareada.** Las dos tecnicas responden las mismas consultas. Para
  saber si una diferencia es mas que azar se usa una prueba de permutacion de
  signos sobre la diferencia por consulta: 10 000 permutaciones con semilla
  fija, bilateral.

## Resultados

<!-- generado por relevancia_texto.py desde 20261019T232944Z_34775e724d824378964e9b9b674d00ad_rankings.csv -->
| Tecnica | N | Palabras | P@10 | P@10 empates prom. | Techo P@10 | R@10 | R@10 empates prom. | Con acierto | Empates en el corte |
|---|---:|---|---:|---:|---:|---:|---:|---:|---:|
| bm25 | 1 000 | 1 | 0.125 | 0.125 | 0.161 | 0.826 | 0.827 | 86/100 | 2 |
| bm25 | 1 000 | 3 | 0.149 | 0.149 | 0.161 | 0.962 | 0.963 | 98/100 | 22 |
| bm25 | 1 000 | 5+ | 0.158 | 0.158 | 0.161 | 0.994 | 0.994 | 100/100 | 36 |
| bm25 | 10 000 | 1 | 0.104 | 0.104 | 0.161 | 0.733 | 0.722 | 77/100 | 29 |
| bm25 | 10 000 | 3 | 0.138 | 0.138 | 0.161 | 0.937 | 0.938 | 97/100 | 51 |
| bm25 | 10 000 | 5+ | 0.151 | 0.151 | 0.161 | 0.985 | 0.985 | 100/100 | 37 |
| bm25 | 100 000 | 1 | 0.053 | 0.053 | 0.161 | 0.384 | 0.393 | 43/100 | 65 |
| bm25 | 100 000 | 3 | 0.128 | 0.129 | 0.161 | 0.883 | 0.886 | 93/100 | 49 |
| bm25 | 100 000 | 5+ | 0.139 | 0.139 | 0.161 | 0.941 | 0.939 | 98/100 | 42 |
| tf_idf | 1 000 | 1 | 0.127 | 0.127 | 0.161 | 0.831 | 0.831 | 86/100 | 0 |
| tf_idf | 1 000 | 3 | 0.152 | 0.152 | 0.161 | 0.968 | 0.968 | 98/100 | 2 |
| tf_idf | 1 000 | 5+ | 0.159 | 0.159 | 0.161 | 0.996 | 0.996 | 100/100 | 2 |
| tf_idf | 10 000 | 1 | 0.115 | 0.115 | 0.161 | 0.781 | 0.781 | 83/100 | 0 |
| tf_idf | 10 000 | 3 | 0.142 | 0.142 | 0.161 | 0.949 | 0.949 | 97/100 | 1 |
| tf_idf | 10 000 | 5+ | 0.152 | 0.152 | 0.161 | 0.987 | 0.987 | 100/100 | 1 |
| tf_idf | 100 000 | 1 | 0.066 | 0.066 | 0.161 | 0.484 | 0.484 | 54/100 | 2 |
| tf_idf | 100 000 | 3 | 0.127 | 0.127 | 0.161 | 0.865 | 0.865 | 90/100 | 11 |
| tf_idf | 100 000 | 5+ | 0.137 | 0.137 | 0.161 | 0.921 | 0.921 | 96/100 | 8 |

| N | Palabras | A - B | Metrica | Diferencia media | Consultas distintas | p |
|---:|---|---|---|---:|---:|---:|
| 1 000 | 1 | bm25 - tf_idf | precision_10 | -0.002 | 1 | 1.000 |
| 1 000 | 1 | bm25 - tf_idf | recall_10 | -0.005 | 1 | 1.000 |
| 1 000 | 1 | bm25 - tf_idf | precision_10_empates | -0.002 | 1 | 1.000 |
| 1 000 | 1 | bm25 - tf_idf | recall_10_empates | -0.004 | 1 | 1.000 |
| 1 000 | 3 | bm25 - tf_idf | precision_10 | -0.003 | 3 | 0.250 |
| 1 000 | 3 | bm25 - tf_idf | recall_10 | -0.006 | 3 | 0.250 |
| 1 000 | 3 | bm25 - tf_idf | precision_10_empates | -0.003 | 4 | 0.253 |
| 1 000 | 3 | bm25 - tf_idf | recall_10_empates | -0.005 | 4 | 0.369 |
| 1 000 | 5+ | bm25 - tf_idf | precision_10 | -0.001 | 1 | 1.000 |
| 1 000 | 5+ | bm25 - tf_idf | recall_10 | -0.001 | 1 | 1.000 |
| 1 000 | 5+ | bm25 - tf_idf | precision_10_empates | -0.001 | 1 | 1.000 |
| 1 000 | 5+ | bm25 - tf_idf | recall_10_empates | -0.001 | 1 | 1.000 |
| 10 000 | 1 | bm25 - tf_idf | precision_10 | -0.011 | 10 | 0.015 |
| 10 000 | 1 | bm25 - tf_idf | recall_10 | -0.048 | 10 | 0.012 |
| 10 000 | 1 | bm25 - tf_idf | precision_10_empates | -0.011 | 14 | 0.001 |
| 10 000 | 1 | bm25 - tf_idf | recall_10_empates | -0.059 | 14 | 0.000 |
| 10 000 | 3 | bm25 - tf_idf | precision_10 | -0.004 | 3 | 0.250 |
| 10 000 | 3 | bm25 - tf_idf | recall_10 | -0.012 | 3 | 0.250 |
| 10 000 | 3 | bm25 - tf_idf | precision_10_empates | -0.004 | 4 | 0.249 |
| 10 000 | 3 | bm25 - tf_idf | recall_10_empates | -0.011 | 4 | 0.249 |
| 10 000 | 5+ | bm25 - tf_idf | precision_10 | -0.001 | 1 | 1.000 |
| 10 000 | 5+ | bm25 - tf_idf | recall_10 | -0.002 | 1 | 1.000 |
| 10 000 | 5+ | bm25 - tf_idf | precision_10_empates | -0.001 | 1 | 1.000 |
| 10 000 | 5+ | bm25 - tf_idf | recall_10_empates | -0.002 | 1 | 1.000 |
| 100 000 | 1 | bm25 - tf_idf | precision_10 | -0.013 | 15 | 0.001 |
| 100 000 | 1 | bm25 - tf_idf | recall_10 | -0.099 | 15 | 0.000 |
| 100 000 | 1 | bm25 - tf_idf | precision_10_empates | -0.013 | 23 | 0.000 |
| 100 000 | 1 | bm25 - tf_idf | recall_10_empates | -0.090 | 23 | 0.001 |
| 100 000 | 3 | bm25 - tf_idf | precision_10 | +0.001 | 7 | 1.000 |
| 100 000 | 3 | bm25 - tf_idf | recall_10 | +0.018 | 7 | 0.309 |
| 100 000 | 3 | bm25 - tf_idf | precision_10_empates | +0.002 | 7 | 0.691 |
| 100 000 | 3 | bm25 - tf_idf | recall_10_empates | +0.021 | 7 | 0.231 |
| 100 000 | 5+ | bm25 - tf_idf | precision_10 | +0.002 | 5 | 0.758 |
| 100 000 | 5+ | bm25 - tf_idf | recall_10 | +0.019 | 5 | 0.312 |
| 100 000 | 5+ | bm25 - tf_idf | precision_10_empates | +0.002 | 7 | 0.487 |
| 100 000 | 5+ | bm25 - tf_idf | recall_10_empates | +0.018 | 7 | 0.205 |

## Lectura de los resultados

### Las dos tecnicas quedan cerca del techo con 3 y 5+ palabras

Con consultas de 3 y 5+ palabras, las dos dejan el duplicado entre los 10
primeros en 90 a 100 de cada 100 consultas, en los tres tamanos: Recall@10 entre
0,865 y 0,996. P@10 queda entre 0,127 y 0,159, es decir entre el 79 % y el 99 %
del techo de 0,161. El corpus mas grande cuesta poco: de 1k a 100k, Recall@10
baja de 0,96–1,00 a 0,87–0,94, porque hay mas preguntas parecidas compitiendo
por los 10 lugares.

### Una sola palabra no alcanza en un corpus grande

La consulta de 1 palabra es la mas discriminativa de la pregunta (#171), y aun
asi se degrada con N. Recall@10 de BM25 pasa de 0,826 a 0,733 y a 0,384; el de
TF-IDF, de 0,831 a 0,781 y a 0,484. Con 100k, esa palabra aparece en 202
documentos en promedio (#172): el duplicado es uno entre cientos que tienen la
misma palabra, y una sola palabra no da como separarlos. Las palabras de mas
de una consulta larga son las que eligen entre ellos.

### TF-IDF frente a BM25: sin diferencia, salvo con una palabra

| Palabras | 1k | 10k | 100k |
|---|---|---|---|
| 1 | igual (1 consulta distinta) | **TF-IDF** (R@10 +0,048, p = 0,012) | **TF-IDF** (R@10 +0,099, p < 0,001) |
| 3 | igual (p = 0,25) | igual (p = 0,25) | igual (p >= 0,31) |
| 5+ | igual (1 consulta distinta) | igual (1 consulta distinta) | igual (p >= 0,31) |

Con 3 y 5+ palabras **no hay diferencia**: las dos tecnicas devuelven el mismo
top-10 en casi todas las consultas (como mucho 7 de 100 distintas) y ninguna
diferencia pasa la prueba. Es lo que anticipaba la nota del issue. Con varias
palabras, lo que decide el ranking es que documentos comparten mas terminos
raros de la consulta, y en eso el idf de las dos coincide: el de BM25,
`ln(1 + (N - df + 0,5) / (df + 0,5))`, y el de TF-IDF, `log10(N / df)`, ordenan
los terminos igual. En corpus chicos la diferencia es todavia menor: con 1k una
consulta tiene en promedio entre 3 y 45 coincidentes (#172), y el top-10 trae
buena parte de ellos.

Con 1 palabra, **TF-IDF es mejor en 10k y 100k**, y la diferencia es
significativa. No es un efecto del desempate: con los empates promediados la
diferencia se mantiene (R@10 +0,059 con 10k y +0,090 con 100k, p <= 0,001),
aunque BM25 empata en el corte en 29 y 65 de las 100 consultas.

Con una sola palabra en la consulta, el idf es el mismo para todos los
candidatos, y el orden entre ellos lo deciden el tf y la normalizacion. BM25
premia que el documento **repita** la palabra: en Quora eso son sobre todo
preguntas comparativas (`CSE at SSN or CSE at NIT`, `NVIDIA GTX 960M to an
NVIDIA GTX 1070`), y un duplicado casi nunca la repite. TF-IDF atenua la
repeticion por dos lados: el tf pasa por `1 + log10 tf`, y la palabra repetida
tambien agranda la norma del documento por la que se divide el coseno. Una
verificacion manual con los 100k, fuera de los CSV oficiales, lo cuantifica.
Entre los 10 primeros de las consultas de 1 palabra, repiten la palabra el
**12,9 %** de los documentos de BM25 y el **3,4 %** de los de TF-IDF; entre los
relevantes, solo el **1,9 %**. Con 10k: 8,9 %, 6,0 % y 1,9 %.

Esto es propio del corpus, no una regla general. En documentos largos, que la
palabra se repita suele indicar que el documento trata de ese tema, y ahi la
saturacion del tf de BM25 (con `k1`) es justamente su ventaja. En preguntas de
una linea, repetir la palabra indica una comparacion, no una pregunta igual.

## Limites

- **Relevante es "duplicado".** Una pregunta parecida pero no identica cuenta
  como no relevante, aunque responda lo mismo. Eso castiga a todas las tecnicas
  por igual.
- **Juicios incompletos.** Si Quora no marco un duplicado real, este cuenta como
  no relevante. Los pares de Quora fueron marcados por personas y no cubren
  todo.
- **Consultas derivadas.** Las de 1 y 3 palabras salen de las palabras mas raras
  de la pregunta (#171): son las mejores palabras posibles, no las que
  escribiria un usuario. Con palabras al azar, las consultas cortas rendirian
  menos.
- **Textos cortos.** Con documentos largos, la normalizacion por longitud de
  BM25 pesa mas y el resultado de la consulta de una palabra podria invertirse.
- **Cien consultas por grupo.** Una diferencia de 0,01 en P@10 equivale a una
  consulta mas con acierto; la prueba pareada es la que dice si eso es azar.
