"""Detectar un rostro y alinearlo (#304).

Pasos 1 y 2 de la lamina de reconocimiento facial de la clase 10:
*Input & Landmark Detection* y *Geometric Alignment*.

YuNet devuelve, por cada rostro, su caja, una confianza y cinco landmarks:
los dos ojos, la nariz y las dos comisuras de la boca. Con esos cinco puntos
se endereza y se escala el recorte hasta dejar el rostro frontal y del mismo
tamano siempre, que es lo que la lamina llama *Normalized, Frontal Face*.

Alinear importa porque el descriptor de #305 no es invariante a la pose ni a
la escala: si no se normaliza, la misma persona de perfil y de frente cae en
puntos distintos del espacio y el umbral de #303 no puede separar nada.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from app.rostros.errores import EmptyObjectError
from app.rostros.lectura import leer_color
from app.rostros.modelos import MODELOS, SFACE, YUNET, traer

UMBRAL_CONFIANZA = 0.9
"""Confianza minima para dar un rostro por bueno.

YuNet es generoso: con el umbral bajo marca manchas que no son caras, y cada
falso rostro entra al dataset como una identidad mas. 0.9 es estricto a
proposito; se afloja desde el parametro cuando una foto cuesta.
"""

LADO_ALINEADO = 112
"""Lado del recorte alineado, en pixeles. Es el tamano de entrada de SFace."""


@dataclass(frozen=True, slots=True)
class RostroDetectado:
    """Un rostro encontrado en una imagen, con lo que hace falta para alinearlo."""

    caja: tuple[int, int, int, int]  # x, y, ancho, alto
    confianza: float
    landmarks: tuple[tuple[int, int], ...]  # ojo der, ojo izq, nariz, boca der, boca izq

    # La fila cruda de 15 numeros que devolvio YuNet. `alignCrop` la quiere
    # tal cual: convertirla a tuplas y volver a armarla solo agrega una
    # oportunidad de equivocarse en el orden de los puntos.
    fila: np.ndarray = field(repr=False)


def crear_detector(
    umbral: float = UMBRAL_CONFIANZA,
    carpeta: Path = MODELOS,
) -> cv2.FaceDetectorYN:
    """Carga YuNet. El tamano de entrada se fija despues, por imagen."""

    return cv2.FaceDetectorYN.create(str(traer(YUNET, carpeta)), "", (320, 320), umbral)


def crear_alineador(carpeta: Path = MODELOS) -> cv2.FaceRecognizerSF:
    """Carga SFace, que ademas de describir sabe alinear.

    Se usa su ``alignCrop`` en vez de calcular la transformacion a mano porque
    el recorte tiene que caer exactamente donde SFace fue entrenado a
    esperarlo. Una alineacion propia, aunque sea razonable, desplazaria los
    rasgos unos pixeles y empeoraria el descriptor sin que nada avise.
    """

    return cv2.FaceRecognizerSF.create(str(traer(SFACE, carpeta)), "")


def detectar(
    imagen: np.ndarray,
    detector: cv2.FaceDetectorYN,
    *,
    ruta: Path | None = None,
) -> tuple[RostroDetectado, ...]:
    """Devuelve todos los rostros de la imagen, el mas confiable primero.

    Una imagen sin rostros lanza ``EmptyObjectError``, no un error de archivo:
    una foto de un paisaje es valida y vacia, y quien carga el dataset la
    anota y sigue.
    """

    alto, ancho = imagen.shape[:2]
    detector.setInputSize((ancho, alto))
    _, caras = detector.detect(imagen)
    if caras is None or len(caras) == 0:
        nombre = ruta.name if ruta is not None else "la imagen"
        raise EmptyObjectError(f"no hay ningun rostro en {nombre}", ruta)

    rostros = []
    for fila in caras:
        x, y, w, h = (int(v) for v in fila[:4])
        puntos = tuple((int(fila[4 + 2 * i]), int(fila[5 + 2 * i])) for i in range(5))
        rostros.append(
            RostroDetectado(
                caja=(x, y, w, h),
                confianza=float(fila[14]),
                landmarks=puntos,
                fila=fila,
            )
        )
    return tuple(sorted(rostros, key=lambda r: r.confianza, reverse=True))


def alinear(
    imagen: np.ndarray,
    rostro: RostroDetectado,
    alineador: cv2.FaceRecognizerSF,
) -> np.ndarray:
    """Devuelve el recorte alineado de 112x112 en BGR."""

    return alineador.alignCrop(imagen, rostro.fila)


def rostros_de(
    ruta: str | Path,
    detector: cv2.FaceDetectorYN,
    alineador: cv2.FaceRecognizerSF,
) -> tuple[np.ndarray, ...]:
    """Lee la imagen y devuelve un recorte alineado por cada rostro."""

    archivo = Path(ruta)
    imagen = leer_color(archivo)
    return tuple(
        alinear(imagen, rostro, alineador) for rostro in detectar(imagen, detector, ruta=archivo)
    )


__all__ = [
    "LADO_ALINEADO",
    "UMBRAL_CONFIANZA",
    "RostroDetectado",
    "alinear",
    "crear_alineador",
    "crear_detector",
    "detectar",
    "rostros_de",
]
