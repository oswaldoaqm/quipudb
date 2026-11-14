"""El vector que describe un rostro (#305).

Paso 3 de la lamina de la clase 10: *Global Descriptor Extraction*. SFace
convierte un recorte alineado de 112x112 en 128 numeros, lo que la
diapositiva llama el *digital fingerprint* de la persona.

**La dimension es 128**, medida sobre el modelo real. Es lo que fija el tipo
de la columna en #302 (``VECTOR(128)``, 512 bytes por vector) y, con el, los
umbrales de #303. ``describir`` lo comprueba en cada llamada en vez de
confiar: si un dia se cambia el modelo, se entera aqui y no al comparar.

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
    """Devuelve el vector de 128 numeros de un recorte alineado.

    El recorte tiene que venir de ``alinear``. Un rostro que no se detecto no
    llega hasta aqui: eso es un error de la etapa anterior, no de esta.
    """

    bruto = np.asarray(descriptor.feature(recorte), dtype=np.float32).ravel()
    if bruto.size != DIMENSIONES:
        raise ValueError(
            f"SFace devolvio {bruto.size} numeros y no {DIMENSIONES}: "
            "cambia el tipo de la columna en #302 antes de seguir"
        )

    if not bruto.any():
        raise EmptyObjectError("el recorte dio un vector nulo, no describe nada")
    return bruto


__all__ = [
    "DIMENSIONES",
    "crear_descriptor",
    "describir",
]
