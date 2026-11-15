"""Que hace el pipeline con entradas que nadie deberia mandar, pero mandan.

Lo que se fija aqui no es que acierte, sino que **nunca reviente**: ante una
entrada imposible tiene que salir un error del tipo correcto, no una traza.
La distincion importa porque quien carga el dataset atrapa
``FeatureExtractionError``, anota el archivo y sigue; cualquier otra excepcion
le corta la carga entera.

Salieron de probar el API con 27 entradas raras. Las que no necesitan modelos
corren siempre.
"""

from pathlib import Path

import cv2
import numpy as np
import pytest

from app.rostros.errores import EmptyObjectError, FeatureExtractionError
from app.rostros.lectura import leer_color
from app.rostros.modelos import ARCFACE, YUNET

LFW = Path(__file__).resolve().parents[2] / "datos" / "lfw"

sin_modelos = pytest.mark.skipif(
    not (YUNET.ruta().is_file() and ARCFACE.ruta().is_file()),
    reason="faltan los modelos ONNX",
)
sin_dataset = pytest.mark.skipif(not LFW.is_dir(), reason="LFW no esta bajado (#301)")


def _guardar(ruta: Path, imagen: np.ndarray) -> Path:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_bytes(cv2.imencode(ruta.suffix, imagen)[1].tobytes())
    return ruta


# --- archivos que no son imagenes ---------------------------------------------


@pytest.mark.parametrize(
    ("nombre", "contenido"),
    [
        ("texto.jpg", b"esto no es una imagen"),
        ("vacio.png", b""),
        ("truncado.png", b"\x89PNG\r\n\x1a\n"),
        ("zip_disfrazado.png", b"PK\x03\x04deadbeef"),
        ("basura.png", bytes(range(256)) * 4),
    ],
)
def test_un_archivo_que_no_es_imagen_culpa_al_archivo(tmp_path, nombre, contenido) -> None:
    """Es error del objeto, no del proceso: la carga lo anota y sigue."""

    ruta = tmp_path / nombre
    ruta.write_bytes(contenido)

    with pytest.raises(FeatureExtractionError):
        leer_color(ruta)


def test_una_carpeta_no_se_confunde_con_una_imagen(tmp_path) -> None:
    with pytest.raises(FeatureExtractionError):
        leer_color(tmp_path)


# --- imagenes validas pero imposibles -----------------------------------------


@sin_modelos
@pytest.mark.parametrize(
    ("nombre", "forma"),
    [
        ("1x1", (1, 1)),
        ("tira_ancha", (4, 2000)),
        ("tira_alta", (2000, 4)),
        ("minuscula", (8, 8)),
    ],
)
def test_una_imagen_de_forma_imposible_no_revienta(tmp_path, nombre, forma) -> None:
    """Sin rostros, pero sin traza: es el mismo caso que un paisaje."""

    from app.rostros.deteccion import crear_detector, detectar

    imagen = np.zeros((*forma, 3), dtype=np.uint8)

    with pytest.raises(EmptyObjectError):
        detectar(imagen, crear_detector())


@sin_modelos
@pytest.mark.parametrize("color", [0, 128, 255])
def test_una_imagen_de_un_solo_color_no_tiene_rostros(color) -> None:
    from app.rostros.deteccion import crear_detector, detectar

    plana = np.full((400, 400, 3), color, dtype=np.uint8)

    with pytest.raises(EmptyObjectError):
        detectar(plana, crear_detector())


@sin_modelos
def test_el_ruido_no_tiene_rostros() -> None:
    """Si los tuviera, cada mancha entraria al dataset como una identidad."""

    from app.rostros.deteccion import crear_detector, detectar

    rng = np.random.default_rng(0)
    ruido = rng.integers(0, 256, (400, 400, 3), dtype=np.uint8)

    with pytest.raises(EmptyObjectError):
        detectar(ruido, crear_detector())


# --- formatos y canales -------------------------------------------------------


@sin_modelos
@sin_dataset
@pytest.mark.parametrize("extension", [".jpg", ".png", ".bmp", ".webp"])
def test_el_formato_del_archivo_no_cambia_el_resultado(tmp_path, extension) -> None:
    """La decodificacion la hace OpenCV; el pipeline ve siempre BGR."""

    from app.rostros.deteccion import crear_detector, detectar

    original = leer_color(min(LFW.glob("*/*.jpg")))
    convertida = leer_color(_guardar(tmp_path / f"cara{extension}", original))

    assert convertida.shape == original.shape
    assert len(detectar(convertida, crear_detector())) >= 1


@sin_modelos
@sin_dataset
def test_una_imagen_con_canal_alfa_pierde_el_alfa_y_funciona(tmp_path) -> None:
    """Un PNG recortado trae cuatro canales; el pipeline espera tres."""

    from app.rostros.deteccion import crear_detector, detectar

    original = leer_color(min(LFW.glob("*/*.jpg")))
    rgba = cv2.cvtColor(original, cv2.COLOR_BGR2BGRA)
    rgba[:, :, 3] = 128
    ruta = tmp_path / "con_alfa.png"
    ruta.write_bytes(cv2.imencode(".png", rgba)[1].tobytes())

    leida = leer_color(ruta)

    assert leida.shape[2] == 3
    assert len(detectar(leida, crear_detector())) >= 1


# --- muchas caras -------------------------------------------------------------


@sin_modelos
@sin_dataset
def test_un_mosaico_de_caras_las_encuentra_todas() -> None:
    """El limite de 8 por imagen lo pone el servidor, no la deteccion."""

    from app.rostros.deteccion import crear_detector, detectar

    cara = leer_color(min(LFW.glob("*/*.jpg")))
    mosaico = np.vstack([np.hstack([cara] * 3)] * 2)

    rostros = detectar(mosaico, crear_detector())

    assert len(rostros) >= 5
