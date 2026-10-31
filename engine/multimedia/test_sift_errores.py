"""Archivos de los que no se pueden sacar descriptores (issue #176).

El criterio 3 del issue pide dos cosas distintas: que el error sea claro y
que no tumbe el proceso. Lo segundo es lo que permite cargar una carpeta de
miles de archivos y terminar con un informe de los que fallaron, en vez de
cortar en el primero, igual que hace la carga masiva de CSV con
``CsvLoadError``.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2", reason="se necesita opencv-python-headless")

from engine.multimedia.sift import FeatureExtractionError, extract_sift

EJEMPLO = Path(__file__).parent / "datos" / "ejemplo.png"


def _png_de_un_solo_color(destino: Path, gris: int = 128, lado: int = 64) -> Path:
    """Escribe un PNG liso, que es el caso sin ningun punto de interes."""

    lienzo = np.full((lado, lado), gris, dtype=np.uint8)
    ok, codificado = cv2.imencode(".png", lienzo)
    assert ok, "no se pudo codificar el PNG de la prueba"
    destino.write_bytes(codificado.tobytes())
    return destino


def test_un_archivo_que_no_es_imagen_da_un_error_claro(tmp_path: Path) -> None:
    ruta = tmp_path / "notas.txt"
    ruta.write_text("esto no es una imagen", encoding="utf-8")
    with pytest.raises(FeatureExtractionError, match="no es una imagen"):
        extract_sift(ruta)


def test_un_archivo_vacio_da_un_error_claro(tmp_path: Path) -> None:
    # Un PNG de cero bytes llega hasta imdecode y volveria como "no es una
    # imagen", que manda a revisar el formato en vez del archivo.
    ruta = tmp_path / "vacio.png"
    ruta.write_bytes(b"")
    with pytest.raises(FeatureExtractionError, match="esta vacio"):
        extract_sift(ruta)


def test_una_ruta_que_no_existe_da_un_error_claro(tmp_path: Path) -> None:
    with pytest.raises(FeatureExtractionError, match="no se puede leer"):
        extract_sift(tmp_path / "fantasma.png")


def test_una_imagen_de_un_solo_color_no_tiene_puntos_de_interes(tmp_path: Path) -> None:
    ruta = _png_de_un_solo_color(tmp_path / "liso.png")
    with pytest.raises(FeatureExtractionError, match="no tiene puntos de interes"):
        extract_sift(ruta)


def test_el_error_dice_que_archivo_fallo(tmp_path: Path) -> None:
    # Quien recorre una carpeta entera necesita nombrar el archivo que salto.
    ruta = _png_de_un_solo_color(tmp_path / "liso.png")
    with pytest.raises(FeatureExtractionError) as error:
        extract_sift(ruta)
    assert error.value.path == ruta


def test_un_archivo_corrupto_da_un_error_claro(tmp_path: Path) -> None:
    # Cabecera PNG valida y el resto basura: es el caso de una descarga a
    # medias, y no lo cubre la prueba del archivo que no es imagen porque
    # aqui el decodificador si reconoce el formato y falla despues.
    ruta = tmp_path / "corrupto.png"
    ruta.write_bytes(EJEMPLO.read_bytes()[:60] + b"\x00" * 200)
    with pytest.raises(FeatureExtractionError, match="no es una imagen"):
        extract_sift(ruta)


def test_una_carpeta_en_vez_de_un_archivo_da_un_error_claro(tmp_path: Path) -> None:
    # read_bytes lanza IsADirectoryError en Linux y PermissionError en
    # Windows: por eso el mensaje se arma con strerror y no con el tipo.
    carpeta = tmp_path / "imagenes"
    carpeta.mkdir()
    with pytest.raises(FeatureExtractionError, match="no se puede leer"):
        extract_sift(carpeta)


def test_una_imagen_sin_puntos_no_tumba_el_proceso(tmp_path: Path) -> None:
    liso = _png_de_un_solo_color(tmp_path / "liso.png")
    extraidos, fallidos = [], []
    for ruta in (liso, EJEMPLO):
        try:
            extraidos.append(extract_sift(ruta))
        except FeatureExtractionError as fallo:
            fallidos.append(fallo.path)
    assert len(extraidos) == 1
    assert fallidos == [liso]
