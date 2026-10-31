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

cv2 = pytest.importorskip("cv2", reason="se necesita opencv-python-headless")

from engine.multimedia.sift import extract_sift, read_grayscale

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


def test_una_imagen_en_color_llega_en_un_solo_canal(tmp_path: Path) -> None:
    # Los datasets reales no son PNG en gris: la conversion la hace
    # IMREAD_GRAYSCALE al decodificar, no el llamador.
    color = cv2.cvtColor(read_grayscale(EJEMPLO), cv2.COLOR_GRAY2BGR)
    ruta = tmp_path / "color.png"
    ok, codificado = cv2.imencode(".png", color)
    assert ok
    ruta.write_bytes(codificado.tobytes())
    assert read_grayscale(ruta).ndim == 2
    assert len(extract_sift(ruta)) > 0


def test_un_jpeg_tambien_se_puede_leer(tmp_path: Path) -> None:
    # Otro decodificador y con perdida. Es el formato de las fotos reales,
    # asi que si solo se probara PNG no se estaria probando el caso de uso.
    ruta = tmp_path / "foto.jpg"
    ok, codificado = cv2.imencode(".jpg", read_grayscale(EJEMPLO))
    assert ok
    ruta.write_bytes(codificado.tobytes())
    assert read_grayscale(ruta).ndim == 2
    assert len(extract_sift(ruta)) > 0
