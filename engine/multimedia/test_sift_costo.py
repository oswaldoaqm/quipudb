"""Acotar cuantos puntos de interes se extraen por imagen (issue #176).

El criterio 2 del issue existe por el volumen: una foto grande da miles de
descriptores y el enunciado pide datasets de hasta 100 000 objetos. Sin
limite, el K-Means de #178 tendria que agrupar cientos de millones de
vectores de 128 valores.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("cv2", reason="se necesita opencv-python-headless")

from engine.multimedia.sift import create_detector, extract_sift, read_grayscale

EJEMPLO = Path(__file__).parent / "datos" / "ejemplo.png"
LIMITE = 3


def test_sin_limite_la_imagen_de_ejemplo_da_mas_descriptores_que_el_limite() -> None:
    # Precondicion de las pruebas de recorte: si la imagen diera menos
    # descriptores que el limite, pasarian por vacuidad sin recortar nada.
    assert len(extract_sift(EJEMPLO)) > LIMITE


def test_max_keypoints_acota_el_numero_de_descriptores() -> None:
    assert len(extract_sift(EJEMPLO, max_keypoints=LIMITE)) == LIMITE


def test_el_tope_de_opencv_no_alcanza_y_por_eso_se_recorta_aparte() -> None:
    # nfeatures es un tope BLANDO: retainBest conserva todos los puntos que
    # empatan con la respuesta del ultimo que entra, y SIFT emite un punto
    # por cada orientacion dominante de la misma ubicacion, todos con la
    # misma respuesta. Pedirle 3 a OpenCV devuelve 5 en esta imagen.
    _, crudos = create_detector(LIMITE).detectAndCompute(read_grayscale(EJEMPLO), None)
    assert len(crudos) > LIMITE
    assert len(extract_sift(EJEMPLO, max_keypoints=LIMITE)) == LIMITE


def test_el_recorte_es_reproducible() -> None:
    # El desempate tiene que ser estable: el codebook de #178 se construye
    # una vez y las consultas se cuantizan contra ese mismo.
    assert np.array_equal(
        extract_sift(EJEMPLO, max_keypoints=LIMITE),
        extract_sift(EJEMPLO, max_keypoints=LIMITE),
    )


def test_un_limite_mayor_que_los_puntos_disponibles_no_inventa_filas() -> None:
    sin_limite = len(extract_sift(EJEMPLO))
    assert len(extract_sift(EJEMPLO, max_keypoints=sin_limite + 100)) == sin_limite


def test_un_max_keypoints_no_positivo_es_un_error_de_programacion() -> None:
    # ValueError y no FeatureExtractionError: el archivo no tiene la culpa.
    with pytest.raises(ValueError, match="max_keypoints"):
        create_detector(0)


def test_un_max_keypoints_negativo_tambien_se_rechaza() -> None:
    with pytest.raises(ValueError, match="max_keypoints"):
        create_detector(-5)
