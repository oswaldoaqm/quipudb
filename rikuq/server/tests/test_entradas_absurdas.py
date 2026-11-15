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

from app.rostros.errores import FeatureExtractionError
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
