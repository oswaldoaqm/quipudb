"""Lectura de imagenes en color para el pipeline de rostros (#304)."""

from pathlib import Path

import cv2
import numpy as np
import pytest

from app.rostros.errores import EmptyObjectError, FeatureExtractionError
from app.rostros.lectura import leer_color


def _escribir_png(ruta: Path, alto: int = 8, ancho: int = 12) -> Path:
    imagen = np.zeros((alto, ancho, 3), dtype=np.uint8)
    imagen[:, :, 0] = 255  # azul: en BGR el primer canal
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_bytes(cv2.imencode(".png", imagen)[1].tobytes())
    return ruta


def test_leer_devuelve_tres_canales_en_bgr(tmp_path: Path) -> None:
    """La deteccion necesita color; SIFT se conformaba con escala de grises."""

    imagen = leer_color(_escribir_png(tmp_path / "azul.png"))

    assert imagen.shape == (8, 12, 3)
    assert imagen.dtype == np.uint8
    assert tuple(imagen[0, 0]) == (255, 0, 0)


def test_leer_funciona_con_tildes_en_la_ruta(tmp_path: Path) -> None:
    """En Windows cv2.imread devuelve None aqui, sin decir por que."""

    ruta = _escribir_png(tmp_path / "fotos de maría" / "ñandú.png")

    assert leer_color(ruta).shape == (8, 12, 3)


def test_leer_un_archivo_que_no_existe_culpa_al_archivo(tmp_path: Path) -> None:
    with pytest.raises(FeatureExtractionError) as error:
        leer_color(tmp_path / "no-esta.png")

    assert error.value.path == tmp_path / "no-esta.png"
    assert "no se puede leer" in str(error.value)


def test_leer_un_archivo_vacio_lo_dice(tmp_path: Path) -> None:
    ruta = tmp_path / "vacio.png"
    ruta.write_bytes(b"")

    with pytest.raises(FeatureExtractionError, match="esta vacio"):
        leer_color(ruta)


def test_leer_algo_que_no_es_imagen_lo_dice(tmp_path: Path) -> None:
    """Un .jpg a medio descargar es un archivo roto, no una foto sin rostros."""

    ruta = tmp_path / "roto.jpg"
    ruta.write_bytes(b"esto no es una imagen")

    with pytest.raises(FeatureExtractionError, match="no es una imagen"):
        leer_color(ruta)


def test_la_imagen_sin_rostros_es_otra_cosa_que_el_archivo_roto() -> None:
    """EmptyObjectError hereda, asi que quien atrapa el general atrapa los dos."""

    assert issubclass(EmptyObjectError, FeatureExtractionError)
    assert not issubclass(FeatureExtractionError, EmptyObjectError)
