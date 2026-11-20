# Robustez de Rikuq

[rikuq_evaluacion.md](rikuq_evaluacion.md) mide si Rikuq acierta y cuánto
tarda con fotos limpias. Este documento mide otra cosa: **qué pasa cuando la
foto no es de catálogo**. El propio informe ya avisaba del hueco:

> Son de **LFW**, fotos de prensa bien iluminadas y frontales [...] La
> precisión sobre las fotos del equipo, tomadas con un celular, puede ser
> distinta.

Lo era. Probando con fotos reales del equipo aparecieron tres problemas, y
los tres están medidos antes y después. Las cifras se reproducen con:

```bash
cd rikuq/server
python ../../benchmarks/scripts/robustez_rikuq.py medir
python ../../benchmarks/scripts/robustez_rikuq.py medir --sin-orientar
python ../../benchmarks/scripts/robustez_rikuq.py dispersion
python ../../benchmarks/scripts/robustez_rikuq.py dispersion --sin-orientar
python ../../benchmarks/scripts/robustez_rikuq.py tabla
```

`--sin-orientar` reproduce el pipeline de antes —sin probar giros y con el
rostro más confiable— para que el antes y el después se midan con la misma
vara y no con dos experimentos distintos.

Son de una máquina del equipo y no de un servidor, con el portátil enchufado:
con batería, el procesador baja a su reloj base y todos los tiempos se
multiplican.

## Cómo se cuenta

Cada caso toma una foto, la degrada y compara su vector con el del original.
Se reportan tres cosas que no son la misma:

- **detecta**: si YuNet llegó a encontrar una cara;
- **distancia media**: cuánto se movió el vector respecto del original;
- **reconoce**: cuántas quedaron dentro del umbral, **sobre el total de
  identidades**, no sobre las que se llegaron a detectar.

Ese último denominador importa. Un caso que solo detecta 10 de 40 caras pero
acierta en 9 de esas 10 no reconoce al 90%: reconoce al 22%, porque las 30 que
ni se detectaron también son rostros perdidos.

La diferencia entre **no detectar** y **detectar mal** es la que más se repite
en lo que sigue. No detectar es el fallo seguro: el sistema dice que no hay
cara. Detectar mal entrega un nombre con un vector que no vale, y nadie se
entera.

## 1. No reconocía caras giradas

Una foto tomada de costado no se reconocía, ni siquiera contra sí misma: la
misma imagen, girada 180°, daba 0.569 de distancia contra su propia copia
registrada, por encima del umbral de 0.55.

`alinear` corrige el giro con los cinco landmarks, así que la teoría estaba
bien. El fallo estaba antes, en la detección: **YuNet solo acierta los
landmarks con la cara más o menos derecha** y, pasados unos 55°, confunde cuál
punto es cada ojo. Lo insidioso es que no avisa — a 60° sigue devolviendo 0.87
de confianza y marca el rostro como fiable.

`detectar_orientado` prueba ocho orientaciones y se queda con la que da el
rostro más confiable. Así la cara nunca queda a más de 22.5° de la vertical,
que el alineado por landmarks ya absorbe.

| Giro | Antes | Después |
|---|---:|---:|
| 15° | 100% | 100% |
| 30° | 98% | **100%** |
| 45° | 30% | **100%** |
| 50° | 20% | **100%** |
| 60° | 5% | **100%** |
| 75° | 0% | **95%** |
| 90° | 0% | **100%** |
| 135° | 0% | **100%** |
| 180° | 0% | **100%** |
| Espejo horizontal | 100% | 100% |
| **Media del grupo** | **35%** | **100%** |

### Lo que costó, y por qué no cuesta más

Sondear las ocho orientaciones a resolución entera costaba **×15**: 306 ms en
un cuadro de cámara de 640×480 y 1 705 ms en una foto de celular. La cámara
pide 3 cuadros por segundo, así que se encolaba y se congelaba.

Para **elegir** la orientación no hace falta la imagen entera. Se sondea sobre
una miniatura de 320 px y se detecta una sola vez, a resolución entera, en la
orientación ganadora:

| | 1 pasada (antes) | 8 a resolución entera | Con sondeo |
|---|---:|---:|---:|
| Cámara 640×480 | 18.3 ms | 306 ms | **81.4 ms** |
| Foto 4608×3456 | 106.1 ms | 1 705 ms | **293.3 ms** |

De ×15 a ×4.4, sin perder precisión. Si la miniatura no encuentra nada — una
cara pequeña dentro de una foto grande se pierde al reducir — se cae al
barrido completo, que es lento pero no deja de funcionar.

Se descartó **bajar la resolución de captura**, que parecía el ahorro obvio.
Ahorra 28 ms pero mueve el vector 0.36, y con un umbral de 0.55 eso se come
casi todo el margen:

| Captura | Detección | Distancia vs 1280 |
|---|---:|---:|
| 1280×720 | 93.5 ms | referencia |
| 960×540 | 82.2 ms | 0.286 |
| 640×360 | 65.0 ms | 0.355 |

La caja del rostro vuelve a las coordenadas de la imagen recibida
(`caja_al_original`): quien consulta dibuja el recuadro sobre la suya, no
sobre la que giramos por dentro.

## 2. Guardaba al acompañante en vez de al sujeto

Al medir la dispersión entre identidades aparecieron personas **más lejos de
sí mismas que de un desconocido**: distancia propia por encima de 1.0, cuando
un extraño típico está a 0.79. Eso no es una cara difícil, es estar midiendo a
otra persona.

Lo era. El cargador tomaba el rostro **más confiable** de cada foto, y las
fotos de LFW a veces traen a alguien más en el encuadre:

| Identidad | Caras foto 1 | Caras foto 2 | Más confiable | **La centrada** |
|---|---:|---:|---:|---:|
| Li_Peng | 1 | 2 | 1.127 | **0.402** |
| Michel_Temer | 1 | 3 | 1.110 | **0.327** |
| Jesse_Jackson | 4 | 4 | 1.021 | **0.351** |

El daño es doble. Se pierde el acierto, y además queda un vector ajeno
guardado bajo un nombre real: esa persona del fondo quedaría identificada como
el sujeto si alguna vez consulta.

Ahora se toma el rostro **más centrado**, porque quien hace una foto encuadra
a quien retrata. De 400 identidades de la muestra, **133 traen más de una
cara** en alguna de sus dos fotos: no es un caso raro.

Para elegir entre las tres reglas posibles se midieron las tres sobre las
mismas fotos:

| Regla | No se reconocen |
|---|---:|
| Más confiable (antes) | 15.0% |
| Más grande | 10.0% |
| **Más centrada** | **9.2%** |

El efecto sobre el pipeline completo está más abajo, en
[el error no se reparte](#el-error-no-se-reparte-se-concentra): **14.0% →
10.5%**, con la mediana casi quieta (0.407 → 0.405). Quien ya se reconocía
bien sigue igual y el cambio está todo en la cola.

No es una ley universal. Vale porque el sujeto suele estar encuadrado, y en
LFW además por construcción. Una foto de grupo sin sujeto claro no la resuelve
nadie desde la imagen sola.

## 3. `ms_total` no medía el servidor

El cronómetro de `/identificar` arrancaba **antes** de leer el cuerpo de la
petición, así que incluía lo que tarda el navegador en codificar y subir el
JPEG. Con la cámara en vivo eso domina el número: el mismo servidor, en el
mismo instante, respondía en 200 ms a un archivo ya en disco y declaraba
**4 954 ms** a un cuadro de cámara.

Ahora la respuesta trae `ms_recepcion` y `ms_proceso` por separado, y
`ms_total` se conserva como la suma. Es la misma frontera de medición que ya
documenta [rikuq_evaluacion.md](rikuq_evaluacion.md): cifras que parecen decir
una cosa y miden otra.

Desglose de un cuadro de cámara de 1280×720, en caliente y con corriente:

| | |
|---|---:|
| Detección con sondeo | 93.5 ms |
| ArcFace | 11.4 ms |
| Búsqueda en el motor | ~24 ms |
| **Proceso** | **~160 ms** |

El requisito del enunciado son **100 ms para la búsqueda en el motor**, y se
cumple con holgura: el arreglo del giro actúa en la detección y no la toca.

## La batería completa

52 degradaciones, 40 identidades de LFW, una foto cada una.

### Lo que aguanta

| Grupo | Hasta dónde |
|---|---|
| Iluminación | brillo de ×0.25 a ×1.8, gamma de 0.4 a 2.5, contraste, contraluz lateral: todo ≥98% |
| Compresión | JPEG hasta calidad 8 → 98% |
| Resolución | hasta el 30% del tamaño → 100% |
| Desenfoque | hasta 21 px → 98% |
| Color | blanco y negro, tinte cálido y tinte frío → 100% |
| Oclusión | gorra 100%, mascarilla 98%, parche opaco del 25% 98% |

La iluminación, que era la preocupación de partida, es donde mejor está.
ArcFace mira estructura, no brillo: el contraluz lateral, que deja media cara
en sombra, apenas mueve el vector 0.037.

### Donde se cae

| Caso | Detecta | Reconoce |
|---|---:|---:|
| Movimiento 25 px | 100% | **8%** |
| Resolución 12% | 78% | **0%** |
| Resolución 8% | 2% | 0% |
| Ruido sigma 40 | 32% | 22% |
| Ruido sigma 60 | **0%** | 0% |
| JPEG calidad 4 | 92% | 38% |
| Parche opaco 40% | 85% | 57% |
| Brillo ×2.4 | 90% | 85% |
| Movimiento 15 px | 100% | 88% |
| Resolución 20% | 100% | 88% |
| Gafas de sol | 100% | 90% |

Dos lecturas de esta tabla:

**Los ojos pesan más que la boca.** Gafas de sol: 90%, con el vector movido
0.426. Mascarilla, que tapa bastante más superficie: 98%, movido 0.286. El
alineado se ancla en los ojos y ArcFace fue entrenado así. Para una demo con
mascarilla el sistema aguanta; con lentes oscuros, bastante menos.

**Hay dos formas de fallar y solo una es segura.** Con ruido fuerte o
resolución mínima el detector **no encuentra cara** (sigma 60 → 0% detecta,
resolución al 8% → 2%) y el sistema dice que no hay rostro, sin equivocarse de
persona. Con movimiento de 25 px detecta el 100% de las veces y reconoce el
8%: entrega vectores inservibles sin avisar. De las 52 degradaciones, esa y el
giro grande —ya corregido— eran las únicas que mentían con confianza.

## Cuando la base crece

Todo lo anterior degrada la **imagen**. Esto degrada la otra variable: el
tamaño de la base. Es lo que de verdad pone a prueba al índice, porque un
escaneo secuencial crecería con ella y HNSW no debería.

Cada identidad entra con una foto y se consulta con otra distinta. Los
tamaños son anidados: solo se agrega lo que falta.

| Rostros en la base | Consultas | Precisión | ms p50 | p95 |
|---:|---:|---:|---:|---:|
| 250 | 120 | 85.0% | **19.61** | 25.22 |
| 1 000 | 120 | 84.2% | **21.33** | 30.23 |

**La base creció 4× y la búsqueda no tardó más**: 19.6 ms contra 21.3 ms,
diferencia dentro del ruido. Es exactamente lo que se le pide a un índice
HNSW, y queda cinco veces por debajo de los 100 ms que pide el enunciado. La
precisión tampoco se degrada.

Dos salvedades:

- **No se llegó a "miles".** LFW tiene 1 680 personas con dos o más fotos, y
  hacen falta dos: una para la base y otra para consultar. Más allá de ahí el
  dataset no da.
- **El 85% es más bajo que el 93.8% de [rikuq_evaluacion.md](rikuq_evaluacion.md)**,
  y no se contradicen: aquí cada identidad entra con **una sola foto** y hay
  que acertar entre 1 000 candidatos, mientras que aquella evaluación usa
  varias fotos por persona y 28 identidades. Es una prueba más dura a
  propósito.

Necesita un motor aparte, con su propio catálogo, porque carga miles de
rostros:

```bash
QUIPUDB_CATALOG=datos/escala/catalogo.txt python -m uvicorn \
    engine.api.main:app --host 127.0.0.1 --port 8005
cd rikuq/server
python ../../benchmarks/scripts/robustez_rikuq.py escala --tamanos 250 1000
```

## El error no se reparte: se concentra

La pregunta de partida era comparar la precisión por tono de piel y por edad.
**No se puede hacer con honestidad**: LFW no trae esas etiquetas, y agrupar a
las personas por lo que uno cree ver en la foto sería inventarse los grupos.

Lo que sí se puede medir sin etiquetar a nadie es la **dispersión**: si el
sistema acierta parejo con todo el mundo o si falla concentrado en unas pocas
identidades. Sobre 400 personas, dos fotos cada una:

| Distancia consigo misma | Antes | Después |
|---|---:|---:|
| mediana | 0.407 | 0.405 |
| decil 9 | 0.609 | **0.550** |
| máximo | 1.059 | **0.984** |
| no se reconocen a sí mismas | 56 (14.0%) | **42 (10.5%)** |

Las dos columnas salen del mismo comando, con y sin `--sin-orientar`.

Pero el número que importa es otro. **El error no se reparte parejo**: el
decil más fácil promedia 0.216 y el más difícil 0.650, tres veces peor. Y de
los 42 fallos que quedan, **40 caen en ese único decil**. Si el sistema
acertara igual con todo el mundo, el peor decil tendría cuatro.

Dicho de otro modo: arreglar el giro y el rostro del sujeto quitó 14 fallos de
los 56, y los 42 que sobreviven están casi todos en la misma décima parte de
las personas. Es una cola, no un ruido repartido.

Por el otro lado, el umbral sí está bien puesto contra los falsos positivos:
el impostor más cercano de las 400 identidades quedó en **0.691**, y
**ninguna** tiene un impostor dentro del umbral de 0.55.

Esto es lo máximo que se puede afirmar con este dataset: **existe un subgrupo
sistemáticamente peor servido**, y no se puede decir cuál. Para nombrarlo
harían falta etiquetas demográficas que LFW no da y que nadie del equipo debe
asignar a ojo. Queda como limitación declarada, no como pregunta resuelta.

## Límites de estas cifras

- Son de **LFW**, con el sesgo conocido de ese dataset. Las degradaciones son
  sintéticas: imitan lo que pasa al tomar una foto, pero no son fotos malas de
  verdad.
- Son de **una máquina**, enchufada y sin nadie más consultando.
- La batería y la dispersión miden **el pipeline**, a fuerza bruta con numpy y
  sin pasar por el motor, para no mezclar el reconocimiento con lo que HNSW
  pueda o no encontrar. Es la misma separación que hace
  [rikuq-umbral.md](../rikuq-umbral.md). La única que sí consulta el motor es
  la de escalabilidad, porque medir el índice es justo su objeto.
- Cada degradación se aplica **sola**. Una foto real llega movida, a
  contraluz y comprimida a la vez, y esta batería no dice qué pasa entonces.
- El reparto del error entre identidades **no se puede atribuir a ningún
  grupo**: hace falta un dataset etiquetado, y asignar esas etiquetas a ojo
  sería peor que no medirlo.
