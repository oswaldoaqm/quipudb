"""Leer una imagen en color para el pipeline de rostros (#304).

A diferencia de SIFT, que trabaja sobre intensidades, la deteccion de rostros
necesita color: YuNet espera tres canales en BGR, que es el orden de OpenCV.
Por lo demas el patron de lectura es el mismo de ``engine/multimedia/sift.py``
y por las mismas razones.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from app.rostros.errores import FeatureExtractionError


def leer_color(ruta: str | Path) -> np.ndarray:
    """Lee la imagen y la devuelve como matriz alto x ancho x 3 en BGR.

    No se usa ``cv2.imread`` con la ruta a proposito. En Windows devuelve
    ``None`` sin avisar cuando la ruta tiene tildes, y ese ``None`` es
    indistinguible del que devuelve ante un archivo que no es una imagen: el
    mensaje de error acabaria culpando al archivo equivocado. Leyendo los
    bytes con ``pathlib`` la ruta la resuelve Python, y a ``imdecode`` solo le
    queda decidir si el contenido es una imagen.

    Importa en este proyecto: el dataset del equipo vive en rutas como
    ``.../QUINTO CICLO/...`` y los nombres de LFW traen tildes.
    """

    archivo = Path(ruta)
    try:
        datos = archivo.read_bytes()
    except OSError as error:
        motivo = error.strerror or type(error).__name__
        raise FeatureExtractionError(
            f"no se puede leer {archivo.name!r}: {motivo}", archivo
        ) from None
    if not datos:
        raise FeatureExtractionError(f"{archivo.name!r} esta vacio", archivo)

    imagen = cv2.imdecode(np.frombuffer(datos, dtype=np.uint8), cv2.IMREAD_COLOR)
    if imagen is None:
        raise FeatureExtractionError(
            f"{archivo.name!r} no es una imagen que OpenCV pueda decodificar", archivo
        )
    return imagen


__all__ = ["leer_color"]
