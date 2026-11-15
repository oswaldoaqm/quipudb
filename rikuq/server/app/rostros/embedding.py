"""El vector que describe un rostro, con ArcFace (#305).

Paso 3 de la lamina de la clase 10: *Global Descriptor Extraction*. ArcFace
convierte un recorte alineado de 112x112 en 512 numeros, lo que la
diapositiva llama el *digital fingerprint* de la persona.

El enunciado lo pide por nombre: "extrae embeddings faciales con modelos
preentrenados (FaceNet, ArcFace)". Se usa ArcFace sobre MobileFaceNet y no
sobre ResNet100 porque la red grande son 261 MB y tarda el triple en CPU; la
chica salio incluso mas rapida que SFace (11,4 ms por rostro contra 13,3).

Corre con ``cv2.dnn``, que ya viene en OpenCV: no hace falta ni ``torch`` ni
``onnxruntime``.

Dos cosas medidas sobre el modelo real, porque ninguna estaba garantizada:

- **La dimension es 512.** Es lo que fija el tipo de la columna en #302
  (``VECTOR(512)``, 2 048 bytes por vector) y, con el, los umbrales de #303.
  ``describir`` lo comprueba en cada llamada en vez de confiar: si un dia se
  cambia el modelo, se entera aqui y no al comparar.
- **ArcFace NO devuelve vectores unitarios.** Su norma ronda 10. Importa
  porque QuipuDB no normaliza al consultar: si se insertara el vector crudo y
  se pidiera distancia por producto punto, el resultado no seria el coseno.
  Se normaliza aqui, antes de guardar, no al consultar.

Con vectores unitarios el producto punto vale lo mismo que el coseno, que es
la equivalencia en la que se apoya #181.

Para ver el vector de una foto, desde ``rikuq/server``::

    python -m app.rostros.embedding foto.jpg
"""

from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

from app.rostros.deteccion import LADO_ALINEADO, alinear, crear_detector, detectar
from app.rostros.constantes import DIMENSIONES
from app.rostros.errores import EmptyObjectError
from app.rostros.lectura import leer_color
from app.rostros.modelos import ARCFACE, MODELOS, traer

# ArcFace espera los pixeles centrados en 0 y escalados a [-1, 1], y en RGB
# aunque OpenCV lea en BGR. Son los valores con los que se entreno; cambiarlos
# no da un error, da vectores peores.
ESCALA = 1.0 / 127.5
MEDIA = (127.5, 127.5, 127.5)


def crear_descriptor(carpeta: Path = MODELOS) -> cv2.dnn.Net:
    """Carga ArcFace con el motor DNN de OpenCV."""

    return cv2.dnn.readNetFromONNX(str(traer(ARCFACE, carpeta)))


def describir(recorte: np.ndarray, descriptor: cv2.dnn.Net) -> np.ndarray:
    """Devuelve el vector unitario de 512 numeros de un recorte alineado.

    El recorte tiene que venir de ``alinear``. Un rostro que no se detecto no
    llega hasta aqui: eso es un error de la etapa anterior, no de esta.
    """

    blob = cv2.dnn.blobFromImage(
        recorte, ESCALA, (LADO_ALINEADO, LADO_ALINEADO), MEDIA, swapRB=True
    )
    descriptor.setInput(blob)
    bruto = np.asarray(descriptor.forward(), dtype=np.float32).ravel()
    if bruto.size != DIMENSIONES:
        raise ValueError(
            f"ArcFace devolvio {bruto.size} numeros y no {DIMENSIONES}: "
            "cambia el tipo de la columna en #302 antes de seguir"
        )

    norma = float(np.linalg.norm(bruto))
    if norma == 0.0:
        raise EmptyObjectError("el recorte dio un vector nulo, no describe nada")
    return bruto / norma


def distancia(uno: np.ndarray, otro: np.ndarray) -> float:
    """Distancia coseno entre dos vectores unitarios: 0 identicos, 2 opuestos.

    Menor es mas parecido, que es la convencion de ``vector_distance`` del
    motor: asi el umbral de #303 se lee como "a lo sumo esta lejos" y no hay
    que recordar si una metrica va al reves que las otras.
    """

    return 1.0 - float(np.dot(uno, otro))


def vectores_de(
    ruta: str | Path,
    detector: cv2.FaceDetectorYN,
    descriptor: cv2.dnn.Net,
) -> tuple[np.ndarray, ...]:
    """Lee la imagen y devuelve un vector por cada rostro que haya."""

    archivo = Path(ruta)
    imagen = leer_color(archivo)
    return tuple(
        describir(alinear(imagen, rostro), descriptor)
        for rostro in detectar(imagen, detector, ruta=archivo)
    )


def main(argv: list[str] | None = None) -> int:
    """Imprime el vector de cada rostro, y la distancia entre los dos primeros."""

    import argparse

    from app.rostros.errores import FeatureExtractionError

    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("imagenes", nargs="+", type=Path)
    parser.add_argument("--completo", action="store_true", help="imprime los 512 numeros")
    args = parser.parse_args(argv)

    detector = crear_detector()
    descriptor = crear_descriptor()

    vistos: list[tuple[Path, np.ndarray]] = []
    problemas = 0
    for ruta in args.imagenes:
        try:
            vectores = vectores_de(ruta, detector, descriptor)
        except FeatureExtractionError as error:
            print(f"  {error}", file=sys.stderr)
            problemas += 1
            continue
        for numero, vector in enumerate(vectores, start=1):
            vistos.append((ruta, vector))
            if args.completo:
                print(f"{ruta.name} #{numero}: {np.array2string(vector, precision=4)}")
            else:
                cabeza = np.array2string(vector[:4], precision=4)
                norma = np.linalg.norm(vector)
                print(f"{ruta.name} #{numero}: {cabeza[:-1]} ...] norma={norma:.4f}")

    if len(vistos) >= 2:
        (una, primera), (otra, segunda) = vistos[0], vistos[1]
        print(f"distancia {una.name} <-> {otra.name}: {distancia(primera, segunda):.4f}")

    return 1 if problemas else 0


__all__ = [
    "DIMENSIONES",
    "crear_descriptor",
    "describir",
    "distancia",
    "main",
    "vectores_de",
]


if __name__ == "__main__":
    sys.exit(main())
