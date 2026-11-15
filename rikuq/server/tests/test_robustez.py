"""Que tanto maltrato aguanta el reconocimiento antes de perder a la persona.

Son pruebas de **regresion**, no de calidad del modelo: fijan lo que hoy se
mide, para que un cambio en el alineado, en el descriptor o en el umbral se
note aqui en vez de descubrirse en la demo.

Cada caso toma una foto de LFW, la maltrata y comprueba que su vector sigue
dentro del umbral de #303 respecto del original. Necesitan los modelos y el
dataset; sin ellos se saltan solas.
"""

from pathlib import Path

import cv2
import numpy as np
import pytest

from app.consulta.identificar import UMBRAL_POR_DEFECTO
from app.rostros.deteccion import alinear, crear_detector, detectar
from app.rostros.embedding import crear_descriptor, describir, distancia
from app.rostros.modelos import ARCFACE, YUNET

LFW = Path(__file__).resolve().parents[2] / "datos" / "lfw"

sin_modelos = pytest.mark.skipif(
    not (YUNET.ruta().is_file() and ARCFACE.ruta().is_file()),
    reason="faltan los modelos ONNX: corre `python -m app.rostros.modelos`",
)
sin_dataset = pytest.mark.skipif(not LFW.is_dir(), reason="LFW no esta bajado (#301)")

pytestmark = [sin_modelos, sin_dataset]


@pytest.fixture(scope="module")
def pipeline():
    """El detector y el descriptor, una sola vez: ArcFace pesa 14 MB."""

    return crear_detector(), crear_descriptor()


@pytest.fixture(scope="module")
def foto() -> np.ndarray:
    from app.rostros.lectura import leer_color

    return leer_color(min(LFW.glob("*/*.jpg")))


def _vector(imagen: np.ndarray, pipeline) -> np.ndarray:
    detector, descriptor = pipeline
    return describir(alinear(imagen, detectar(imagen, detector)[0]), descriptor)


def _se_mantiene(original: np.ndarray, maltratada: np.ndarray, pipeline) -> float:
    """Distancia entre la foto original y su version maltratada."""

    return distancia(_vector(original, pipeline), _vector(maltratada, pipeline))


# --- geometria ----------------------------------------------------------------


def test_el_espejo_no_cambia_a_la_persona(foto, pipeline) -> None:
    """Una foto volteada es la misma cara; el alineado lo absorbe."""

    assert _se_mantiene(foto, cv2.flip(foto, 1), pipeline) < UMBRAL_POR_DEFECTO


@pytest.mark.parametrize("grados", [5, 15, 30])
def test_la_cabeza_inclinada_se_sigue_reconociendo(foto, pipeline, grados) -> None:
    """Para esto sirve alinear con los cinco landmarks y no recortar la caja."""

    alto, ancho = foto.shape[:2]
    matriz = cv2.getRotationMatrix2D((ancho / 2, alto / 2), grados, 1)
    girada = cv2.warpAffine(foto, matriz, (ancho, alto))

    assert _se_mantiene(foto, girada, pipeline) < UMBRAL_POR_DEFECTO


# --- calidad de imagen --------------------------------------------------------


@pytest.mark.parametrize("calidad", [10, 30, 70])
def test_la_compresion_jpeg_no_pierde_a_la_persona(foto, pipeline, calidad) -> None:
    """Una foto de WhatsApp llega muy comprimida."""

    datos = cv2.imencode(".jpg", foto, [cv2.IMWRITE_JPEG_QUALITY, calidad])[1]
    comprimida = cv2.imdecode(datos, cv2.IMREAD_COLOR)

    assert _se_mantiene(foto, comprimida, pipeline) < UMBRAL_POR_DEFECTO


@pytest.mark.parametrize("lado", [7, 21])
def test_el_desenfoque_no_pierde_a_la_persona(foto, pipeline, lado) -> None:
    """La camara en vivo entrega cuadros movidos."""

    assert _se_mantiene(foto, cv2.GaussianBlur(foto, (lado, lado), 0), pipeline) < (
        UMBRAL_POR_DEFECTO
    )


def test_el_ruido_no_pierde_a_la_persona(foto, pipeline) -> None:
    """Poca luz en la webcam se traduce en ruido."""

    rng = np.random.default_rng(0)
    sucia = np.clip(foto.astype(np.int16) + rng.normal(0, 25, foto.shape), 0, 255)

    assert _se_mantiene(foto, sucia.astype(np.uint8), pipeline) < UMBRAL_POR_DEFECTO
