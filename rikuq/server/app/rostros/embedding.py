"""El vector que describe un rostro (#305).

Paso 3 de la lamina de la clase 10: *Global Descriptor Extraction*. SFace
convierte un recorte alineado de 112x112 en 128 numeros, lo que la
diapositiva llama el *digital fingerprint* de la persona.

Dos cosas medidas sobre el modelo real, porque ninguna estaba garantizada:

- **La dimension es 128.** Es lo que fija el tipo de la columna en #302
  (``VECTOR(128)``, 512 bytes por vector) y, con el, los umbrales de #303.
  ``describir`` lo comprueba en cada llamada en vez de confiar: si un dia se
  cambia el modelo, se entera aqui y no al comparar.
- **SFace NO devuelve vectores unitarios.** Sus normas rondan 4 y 5. Importa
  porque ``cv2.match`` normaliza por dentro, pero QuipuDB no: si se insertara
  el vector crudo y se pidiera distancia por producto punto, el resultado no
  seria el coseno. Se normaliza aqui, antes de guardar, no al consultar.

Con vectores unitarios el producto punto vale lo mismo que el coseno, que es
la equivalencia en la que se apoya #181.

"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from app.rostros.deteccion import crear_alineador
from app.rostros.errores import EmptyObjectError
from app.rostros.modelos import MODELOS

DIMENSIONES = 128
"""Largo del vector que devuelve SFace. Medido, no supuesto."""


def crear_descriptor(carpeta: Path = MODELOS) -> cv2.FaceRecognizerSF:
    """Devuelve el SFace que describe.

    Es el mismo objeto que alinea: SFace hace las dos cosas. Se expone con
    otro nombre porque son dos pasos distintos de la lamina, pero conviene
    crear uno solo y pasarlo a los dos: el modelo pesa 37 MB.
    """

    return crear_alineador(carpeta)


def describir(recorte: np.ndarray, descriptor: cv2.FaceRecognizerSF) -> np.ndarray:
    """Devuelve el vector unitario de 128 numeros de un recorte alineado.

    El recorte tiene que venir de ``alinear``. Un rostro que no se detecto no
    llega hasta aqui: eso es un error de la etapa anterior, no de esta.
    """

    bruto = np.asarray(descriptor.feature(recorte), dtype=np.float32).ravel()
    if bruto.size != DIMENSIONES:
        raise ValueError(
            f"SFace devolvio {bruto.size} numeros y no {DIMENSIONES}: "
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


__all__ = [
    "DIMENSIONES",
    "crear_descriptor",
    "describir",
    "distancia",
]
