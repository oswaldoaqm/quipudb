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
from app.rostros.deteccion import (
    alinear,
    crear_detector,
    detectar,
    detectar_orientado,
    girar,
    puntos_al_original,
)
from app.rostros.embedding import crear_descriptor, describir, distancia
from app.rostros.errores import EmptyObjectError
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


def _vector_orientado(imagen: np.ndarray, pipeline) -> np.ndarray:
    """Como ``_vector``, pero probando giros antes de alinear."""

    detector, descriptor = pipeline
    hallazgo = detectar_orientado(imagen, detector)
    return describir(alinear(hallazgo.imagen, hallazgo.rostros[0]), descriptor)


def _se_mantiene_orientando(original: np.ndarray, maltratada: np.ndarray, pipeline) -> float:
    return distancia(_vector(original, pipeline), _vector_orientado(maltratada, pipeline))


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


@pytest.mark.parametrize("grados", [60, 90])
def test_sin_orientar_un_giro_grande_pierde_a_la_persona(foto, pipeline, grados) -> None:
    """El limite medido de detectar sin probar giros, y por que existe.

    ``alinear`` corrige el giro, pero solo si los landmarks estan bien puestos,
    y YuNet deja de ponerlos bien pasados unos 55 grados: confunde cual punto
    es cada ojo. Medido sobre 40 identidades de LFW, a 60 grados se reconoce el
    0%.

    Se pierde de dos maneras distintas y aqui valen las dos, porque no son
    igual de graves. A 90 grados muchas veces **no se detecta nada**, que es el
    fallo seguro: el sistema dice que no hay cara. A 60 todavia se detecta, con
    0.87 de confianza, y devuelve un vector inservible marcado como fiable:
    ese es el fallo que miente.

    Esta prueba fija el limite. Si alguna vez ``detectar`` pasa a ser
    invariante al giro por su cuenta, aqui fallara: entonces se borra, no se
    afloja.
    """

    try:
        distancia_girada = _se_mantiene(foto, girar(foto, grados), pipeline)
    except EmptyObjectError:
        return  # no detecto nada: tampoco reconoce, y ademas avisa
    assert distancia_girada > UMBRAL_POR_DEFECTO


@pytest.mark.parametrize("grados", list(range(0, 360, 30)))
def test_la_vuelta_completa_se_reconoce_al_orientar(foto, pipeline, grados) -> None:
    """Lo que arregla ``detectar_orientado``: la vuelta entera, no 55 grados.

    Probando las ocho orientaciones la cara nunca queda a mas de 22.5 grados
    de la vertical, que es zona comoda para el alineado por landmarks.
    """

    assert _se_mantiene_orientando(foto, girar(foto, grados), pipeline) < UMBRAL_POR_DEFECTO


def test_la_caja_vuelve_a_las_coordenadas_de_la_imagen_recibida(foto, pipeline) -> None:
    """Quien consulta dibuja sobre su imagen, no sobre la que se giro dentro."""

    detector, _ = pipeline
    girada = girar(foto, 90)
    alto, ancho = girada.shape[:2]

    hallazgo = detectar_orientado(girada, detector)
    x, y, lado_x, lado_y = hallazgo.cajas_originales[0]

    assert hallazgo.grados != 0, "una foto de costado tiene que leerse girada"
    assert 0 <= x < x + lado_x <= ancho
    assert 0 <= y < y + lado_y <= alto


def test_una_foto_derecha_no_se_gira_ni_mueve_su_caja(foto, pipeline) -> None:
    """El caso comun no cambia: misma orientacion y misma caja que antes."""

    detector, _ = pipeline
    hallazgo = detectar_orientado(foto, detector)

    assert hallazgo.grados == 0
    assert hallazgo.cajas_originales[0] == detectar(foto, detector)[0].caja


@pytest.mark.parametrize("grados", [0, 45, 90, 135, 180, 225, 270, 315])
def test_deshacer_el_giro_devuelve_el_punto_a_su_sitio(grados) -> None:
    """La inversa de coordenadas, sin modelos de por medio.

    Se marca un punto, se gira la imagen, se busca donde quedo la marca y se
    deshace: tiene que volver a donde estaba. Los multiplos de 90 son exactos;
    las diagonales interpolan y dejan una fraccion de pixel.
    """

    alto, ancho, (cx, cy) = 300, 200, (55, 70)
    imagen = np.zeros((alto, ancho, 3), np.uint8)
    imagen[cy - 6 : cy + 7, cx - 6 : cx + 7] = 255

    ys, xs = np.nonzero(girar(imagen, grados)[:, :, 0] > 127)
    marca = np.array([[xs.mean(), ys.mean()]], dtype=np.float64)
    vuelta = puntos_al_original(marca, (alto, ancho), grados)[0]

    assert np.hypot(vuelta[0] - cx, vuelta[1] - cy) < 1.0


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


# --- luz y color --------------------------------------------------------------


@pytest.mark.parametrize("factor", [0.3, 1.8])
def test_la_luz_no_pierde_a_la_persona(foto, pipeline, factor) -> None:
    """Un salon oscuro y una ventana a contraluz."""

    cambiada = np.clip(foto.astype(np.float32) * factor, 0, 255).astype(np.uint8)

    assert _se_mantiene(foto, cambiada, pipeline) < UMBRAL_POR_DEFECTO


def test_el_blanco_y_negro_no_pierde_a_la_persona(foto, pipeline) -> None:
    """ArcFace mira estructura, no color."""

    gris = cv2.cvtColor(cv2.cvtColor(foto, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR)

    assert _se_mantiene(foto, gris, pipeline) < UMBRAL_POR_DEFECTO


# --- donde si se rompe --------------------------------------------------------


def test_una_foto_destruida_no_se_detecta_en_vez_de_inventar(foto, pipeline) -> None:
    """Con JPEG de calidad 2 no queda cara; mejor no detectar que adivinar."""

    from app.rostros.errores import EmptyObjectError

    datos = cv2.imencode(".jpg", foto, [cv2.IMWRITE_JPEG_QUALITY, 2])[1]
    destruida = cv2.imdecode(datos, cv2.IMREAD_COLOR)

    with pytest.raises(EmptyObjectError):
        _vector(destruida, pipeline)


def test_otra_persona_queda_lejos(pipeline) -> None:
    """El contraste que hace util al umbral: lo propio cerca, lo ajeno lejos."""

    from app.rostros.lectura import leer_color

    carpetas = sorted(p for p in LFW.iterdir() if p.is_dir())
    una = _vector(leer_color(min(carpetas[0].glob("*.jpg"))), pipeline)
    otra = _vector(leer_color(min(carpetas[1].glob("*.jpg"))), pipeline)

    assert distancia(una, otra) > UMBRAL_POR_DEFECTO
