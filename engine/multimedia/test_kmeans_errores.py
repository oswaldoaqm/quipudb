"""Datos y parametros con los que no se puede construir un vocabulario (#178).

Todos fallan antes de agrupar nada. Es a proposito: K-Means sobre un dataset
de cien mil objetos tarda, y descubrir a los diez minutos que K estaba mal
escrito cuesta mucho mas que comprobarlo al entrar.
"""

from __future__ import annotations

import numpy as np
import pytest

from engine.multimedia.kmeans import CodebookError, build_codebook

DATOS = np.array([[0.0, 0.0], [1.0, 1.0], [10.0, 10.0], [11.0, 11.0]], dtype=np.float32)


# ---------------------------------------------------------------------------
# K
# ---------------------------------------------------------------------------


def test_un_k_no_positivo_se_rechaza() -> None:
    with pytest.raises(CodebookError, match="k tiene que ser positivo"):
        build_codebook(DATOS, k=0)


def test_un_k_negativo_se_rechaza() -> None:
    with pytest.raises(CodebookError, match="k tiene que ser positivo"):
        build_codebook(DATOS, k=-3)


def test_pedir_mas_palabras_que_descriptores_se_rechaza() -> None:
    # Con K mayor que N sobran centroides sin un solo punto que describir.
    with pytest.raises(CodebookError, match="hacen falta al menos"):
        build_codebook(DATOS, k=10)


def test_k_igual_al_numero_de_descriptores_funciona() -> None:
    # Caso limite valido: cada descriptor es su propia palabra.
    assert build_codebook(DATOS, k=len(DATOS), seed=0).k == len(DATOS)


# ---------------------------------------------------------------------------
# Forma de los datos
# ---------------------------------------------------------------------------


def test_un_arreglo_de_una_dimension_se_rechaza() -> None:
    with pytest.raises(CodebookError, match="matriz de N x D"):
        build_codebook(np.arange(10, dtype=np.float32), k=2)


def test_una_matriz_sin_filas_se_rechaza() -> None:
    with pytest.raises(CodebookError, match="no hay descriptores"):
        build_codebook(np.empty((0, 128), dtype=np.float32), k=2)


def test_una_matriz_sin_columnas_se_rechaza() -> None:
    with pytest.raises(CodebookError, match="no hay descriptores"):
        build_codebook(np.empty((10, 0), dtype=np.float32), k=2)


# ---------------------------------------------------------------------------
# Valores imposibles
# ---------------------------------------------------------------------------


def test_un_descriptor_con_nan_se_rechaza() -> None:
    # Un NaN contamina el promedio de su centroide y de ahi a todo el
    # vocabulario, sin que ninguna etapa posterior avise.
    datos = DATOS.copy()
    datos[1, 0] = np.nan
    with pytest.raises(CodebookError, match="infinitos o NaN"):
        build_codebook(datos, k=2)


def test_un_descriptor_con_infinito_se_rechaza() -> None:
    # Es justo lo que produciria un MFCC sin el piso del logaritmo (#177).
    datos = DATOS.copy()
    datos[2, 1] = -np.inf
    with pytest.raises(CodebookError, match="infinitos o NaN"):
        build_codebook(datos, k=2)


def test_descriptores_todos_iguales_no_cuelgan_el_algoritmo() -> None:
    # Sin puntos distintos que repartir, k-means++ se queda sin candidatos:
    # tiene que terminar igual, no girar hasta agotar las iteraciones.
    iguales = np.ones((20, 4), dtype=np.float32)
    assert build_codebook(iguales, k=3, seed=0).k == 3


# ---------------------------------------------------------------------------
# Muestreo e iteraciones
# ---------------------------------------------------------------------------


def test_una_muestra_menor_que_k_se_rechaza() -> None:
    # Si entraran menos descriptores que centroides, el error apareceria
    # recien despues de muestrear y culparia a los datos en vez de al limite.
    with pytest.raises(CodebookError, match="max_descriptors"):
        build_codebook(DATOS, k=3, max_descriptors=2)


def test_cero_iteraciones_se_rechaza() -> None:
    with pytest.raises(CodebookError, match="max_iterations"):
        build_codebook(DATOS, k=2, max_iterations=0)


def test_una_sola_iteracion_devuelve_un_vocabulario_valido() -> None:
    # Cortar temprano da peores centroides, pero no un codebook roto.
    codebook = build_codebook(DATOS, k=2, seed=0, max_iterations=1)
    assert codebook.centroids.shape == (2, 2)
    assert np.isfinite(codebook.centroids).all()
