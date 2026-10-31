"""Lectura de la imagen antes de pasarsela a SIFT (issue #176).

``read_grayscale`` existe en vez de llamar a ``cv2.imread`` por un detalle
que cuesta caro: en Windows, ``imread`` devuelve ``None`` sin avisar cuando
la ruta tiene tildes o enes, y ese ``None`` es el mismo que devuelve ante un
archivo que no es una imagen. El mensaje de error acabaria culpando al
formato del archivo cuando el problema es el nombre de la carpeta.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("cv2", reason="se necesita opencv-python-headless")

from engine.multimedia.sift import read_grayscale

EJEMPLO = Path(__file__).parent / "datos" / "ejemplo.png"


def test_la_imagen_se_lee_en_un_solo_canal() -> None:
    # SIFT se define sobre intensidades: si llegara en color, la diferencia
    # de gaussianas se calcularia sobre tres canales y los puntos no
    # calzarian entre si.
    assert read_grayscale(EJEMPLO).ndim == 2


def test_la_imagen_se_lee_como_bytes_sin_signo() -> None:
    assert read_grayscale(EJEMPLO).dtype.name == "uint8"


def test_una_ruta_con_tildes_se_lee_igual(tmp_path: Path) -> None:
    ruta = tmp_path / "fotografía ñandú.png"
    ruta.write_bytes(EJEMPLO.read_bytes())
    assert read_grayscale(ruta).ndim == 2


def test_una_carpeta_con_tildes_se_lee_igual(tmp_path: Path) -> None:
    # El caso real: la carpeta del dataset, no el archivo.
    carpeta = tmp_path / "imágenes de prueba"
    carpeta.mkdir()
    ruta = carpeta / "ejemplo.png"
    ruta.write_bytes(EJEMPLO.read_bytes())
    assert read_grayscale(ruta).ndim == 2
