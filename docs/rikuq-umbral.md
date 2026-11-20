# El umbral de Rikuq, elegido con datos

Cuánto se puede parecer un rostro a otro para decir que son la misma persona
(#303). El valor es **0.55** de distancia coseno, y abajo está de dónde sale.

## Por qué hace falta un umbral

El vecino más cercano **siempre existe**. Si solo se pidiera el más parecido,
una cara que nunca se cargó se identificaría como la persona que casualmente
quedó más cerca, y el sistema nunca diría "no sé quién es".

La lámina de la clase 10 lo separa en dos cosas, y conviene no mezclarlas:

| | decide | dónde vive |
|---|---|---|
| Umbral de confianza de la **detección** | si hay una cara | `app/rostros/deteccion.py`, 0.7 |
| Umbral de **identificación** | de quién es esa cara | `app/consulta/identificar.py`, 0.55 |

## Cómo se midió

```bash
python -m app.consulta.umbral --identidades 40
```

- **40 identidades de LFW** con al menos 3 fotos cada una, 1 755 imágenes.
- **28 identidades entran a la base**; de cada una se reserva su última foto
  para consultar, así que ninguna foto está cargada y consultada a la vez. Si
  lo estuviera, la distancia sería 0 y el experimento diría que todo funciona.
- **12 identidades quedan fuera como intrusos**: sus 309 fotos tienen que
  salir *desconocido*. Sin ellas no hay forma de medir falsos positivos, que
  es la mitad del problema.
- El barrido **no pasa por el índice**: compara a fuerza bruta con numpy. Así
  mide el umbral sin mezclarlo con lo que HNSW pueda o no encontrar. Con
  Recall@10 de 0.99, el valor vale igual al consultar con el índice.

## La curva

Con **ArcFace** (512 dimensiones):

| umbral | aciertos | confunde | rechaza | falsos+ | precisión | balance |
|---:|---:|---:|---:|---:|---:|---:|
| 0.25 | 9 | 0 | 19 | 0 | 32.1% | 66.1% |
| 0.30 | 18 | 0 | 10 | 0 | 64.3% | 82.1% |
| 0.35 | 22 | 0 | 6 | 0 | 78.6% | 89.3% |
| 0.45 | 25 | 0 | 3 | 0 | 89.3% | 94.6% |
| 0.50 | 25 | 0 | 3 | 0 | 89.3% | 94.6% |
| **0.55** | **26** | **0** | **2** | **0** | **92.9%** | **96.4%** |
| 0.60 | 26 | 0 | 2 | 0 | 92.9% | 96.4% |
| 0.65 | 26 | 0 | 2 | 0 | 92.9% | 96.4% |
| 0.70 | 26 | 0 | 2 | 4 | 92.9% | 95.8% |
| 0.75 | 26 | 0 | 2 | 34 | 92.9% | 90.9% |
| 0.80 | 26 | 1 | 1 | 173 | 92.9% | 68.4% |
| 0.85 | 26 | 2 | 0 | 304 | 92.9% | 47.2% |

- **confunde**: estaba en la base, pero se le puso el nombre de otra persona.
- **rechaza**: estaba en la base y salió desconocido.
- **falsos+**: no estaba en la base y se le puso un nombre.
- **balance**: promedio de precisión y rechazo correcto de intrusos. Se
  promedian en vez de sumar aciertos a secas porque, si no, gana el umbral
  que acierta mucho *porque acepta todo*: ese tiene precisión alta y rechazo
  de intrusos nulo.

## Por qué 0.55

- De **0.55 a 0.65 el resultado es idéntico**: 92.9% de precisión y cero
  falsos positivos. Se toma el extremo estricto porque **equivocarse de
  persona es peor que no reconocerla**: un "no sé quién es" se vuelve a
  intentar, y un nombre equivocado no se nota.
- A partir de **0.70 empiezan los falsos positivos** (4 de 309), y a 0.80 ya
  son 173: el sistema le pone nombre a casi cualquiera.
- Por debajo de 0.45 se pierden aciertos sin ganar nada: los falsos positivos
  ya eran cero.

## El umbral depende del modelo

La primera medición, con **SFace**, dio **0.45**. Al cambiar a ArcFace —que es
lo que el enunciado pide por nombre— hubo que rehacerla entera: cambiar de
descriptor cambia el espacio vectorial, y reusar el número viejo habría sido
un error silencioso.

De paso, ArcFace resultó con **mejor margen**: sus falsos positivos empiezan a
0.70 y son solo 4, mientras que SFace ya tenía 16 en 0.60. La banda segura es
más ancha, que es justo lo que uno quiere de un umbral.

## Lo que la curva no arregla

**La precisión se estanca en 92.9%.** Dos de las 28 consultas no se reconocen
con ningún umbral por debajo de 0.70: su propia foto quedó más lejos de las
suyas que el límite. Eso no es problema de umbral sino de las fotos —perfil
muy marcado, oclusión o iluminación muy distinta— y subir el umbral para
rescatarlas costaría falsos positivos. Es el intercambio que el sistema no
puede evitar, y conviene decirlo en el informe en vez de presentar el 92.9%
como si fuera el techo del método.

**Es una muestra de 40 identidades de LFW.** Con las 158 del dataset el valor
podría moverse; el comando está para volver a correrlo. Y LFW son fotos de
prensa, bien iluminadas y sesgadas hacia rostros de hombres blancos: las del
equipo, tomadas con un celular en un salón, pueden necesitar un umbral
distinto. Conviene rehacer la medición cuando estén cargadas.

**Las fotos del barrido están derechas y de frente.** LFW las recorta así por
construcción, de modo que el 0.55 está medido sobre el caso fácil de la
geometría. Las degradaciones que una foto real sí trae —giro, movimiento,
oclusión, poca resolución— mueven el vector lo suficiente para comerse el
margen: con la cara girada 60° la distancia pasa de 0.17 a 0.84, y con un
barrido de 25 px solo el 5% se sigue reconociendo. El umbral no cambia por
eso, pero lo que se puede afirmar de él sí: vale **para fotos derechas y
nítidas**, y fuera de ahí el límite lo pone la calidad de la imagen, no la
curva. Está medido en
[docs/informe/rikuq_robustez.md](informe/rikuq_robustez.md).
