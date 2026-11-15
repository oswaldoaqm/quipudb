# Los modelos de Rikuq, y por qué esos

Qué red hace cada paso del pipeline de reconocimiento facial, y de dónde sale
cada decisión. Los pasos son los cuatro de la lámina de la clase 10.

| paso | modelo | tamaño | ms por rostro |
|---|---|---|---|
| Detección y landmarks | YuNet | 230 KB | ~3 |
| Alineación geométrica | — (transformada de semejanza) | — | <1 |
| Descriptor global | **ArcFace** (MobileFaceNet) | 14 MB | 11,4 |
| Comparación y umbral | — (coseno, HNSW) | — | <1 |

Ninguno se versiona en git; sí se versiona su SHA-256, que es lo que permite
afirmar que todos corremos el mismo modelo. Se traen con:

```bash
python -m app.rostros.modelos
```

## Detección: YuNet

El enunciado dice *"detecta rostros (OpenCV o MTCNN)"*. YuNet es el detector
que trae OpenCV, así que cumple sin agregar dependencias. Devuelve caja,
confianza y los cinco landmarks que el paso siguiente necesita; MTCNN habría
obligado a traer otro marco entero para lo mismo.

El umbral de confianza por omisión es **0.9**, que es estricto. Las fotos de
LFW salen entre 0.92 y 0.93, o sea justo encima. Con el umbral bajo YuNet
marca manchas que no son caras, y cada falso rostro entraría al dataset como
una identidad más.

## Descriptor: ArcFace, no SFace

El enunciado lo pide por nombre:

> extrae embeddings faciales con modelos preentrenados (**FaceNet, ArcFace**)

La primera versión usó **SFace**, que viene dentro de OpenCV y era más cómodo.
Se cambió a ArcFace para ceñirse al enunciado. Medido sobre la misma máquina:

| | dimensiones | ms por rostro | 4 324 imágenes |
|---|---|---|---|
| **ArcFace** (MobileFaceNet) | 512 | **11,4** | 49 s |
| SFace | 128 | 13,3 | 58 s |

**ArcFace resultó más rápido**, no más lento, así que el cambio no costó nada
en rendimiento. Lo que sí costó son bytes: el vector pasó de 512 a 2 048, y el
registro de `rostros` de 720 a 2 256.

### Por qué MobileFaceNet y no ResNet100

ArcFace es el **método de entrenamiento**, no una red concreta. El modelo
oficial del ONNX Model Zoo usa ResNet100: son 261 MB y tarda unas tres veces
más en CPU. El de insightface sobre MobileFaceNet son 14 MB y da el mismo
espacio de 512 dimensiones.

Para una demo que tiene que correr en las laptops del equipo y reconocer en
vivo desde la cámara, la red chica es la decisión correcta. Si alguna vez hay
máquina para la grande, se cambia una entrada en `app/rostros/modelos.py`.

### Procedencia

El ONNX suelto se baja del repositorio `immich-app/buffalo_s` en HuggingFace,
que espeja los paquetes de **insightface** —los autores de ArcFace—, que los
publica comprimidos junto con otros modelos que no hacen falta.

## Alineación: sin modelo

Entre detectar y describir hay que enderezar la cara. ArcFace espera que los
cinco landmarks caigan en posiciones concretas de un recorte de 112×112; esa
plantilla está publicada (`arcface_src` de insightface) y se aplica con una
transformada de semejanza —rotación, escala y traslación, sin deformar la
cara— que es lo que la lámina llama *Geometric Alignment*.

Antes esto lo hacía `FaceRecognizerSF.alignCrop`. Al soltar SFace había que
rehacerlo, y se comprobó que el resultado es **idéntico píxel a píxel** al que
daba OpenCV: usaba la misma plantilla. Así que el cambio no movió nada.

Un detalle que sí movía el resultado: los landmarks hay que tomarlos **sin
redondear**. Usar los enteros que se muestran en pantalla desplazaba el
recorte lo suficiente para que el vector cambiara.

## Lo que ninguno de los dos arregla

Los dos modelos vienen entrenados con datasets públicos de caras, que están
sesgados hacia rostros de hombres blancos —LFW es el ejemplo clásico de ese
problema—. La precisión que mide #306 es la precisión **sobre LFW**, y no hay
que presentarla como si fuera la precisión del sistema para cualquier persona.
Conviene decirlo en el informe.
