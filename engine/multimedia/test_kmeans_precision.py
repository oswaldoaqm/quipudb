"""Precision de la asignacion cuando dos palabras quedan casi juntas (#178).

El asignador no resta los vectores punto a punto -- pediria una matriz de
N x K x D, mas de dos gigabytes por bloque con 8192 descriptores, 256
palabras y 128 valores --, sino que usa la expansion

    ||x - c||^2 = ||x||^2 - 2 x.c + ||c||^2

Esa expansion resta numeros grandes para obtener uno chico, y ahi es donde
la precision se pierde. Con descriptores de SIFT, que van de 0 a 255 en 128
componentes, ``||c||^2`` ronda los tres millones y la resolucion de float32
a esa magnitud es de 0.25: dos centroides separados por menos que eso dejan
de distinguirse.

No es un caso rebuscado. Pasa siempre que K es mayor que la estructura real
de los datos, que es justo lo que ocurre con vocabularios grandes sobre
zonas densas del espacio.
"""

from __future__ import annotations

import numpy as np

from engine.multimedia.kmeans import Codebook, build_codebook, huella_de

ESCALA_SIFT = 255
DIMENSION = 128


def _exacto(datos: np.ndarray, centroides: np.ndarray) -> np.ndarray:
    """Resta directa en float64: la referencia contra la que se compara."""

    diferencias = datos[:, None, :].astype(np.float64) - centroides[None, :, :].astype(np.float64)
    return np.argmin((diferencias**2).sum(axis=2), axis=1)


def _codebook(centroides: np.ndarray) -> Codebook:
    matriz = np.ascontiguousarray(centroides, dtype=np.float32)
    return Codebook(matriz, 0, huella_de(matriz))


def test_la_asignacion_coincide_con_la_resta_directa_en_datos_sueltos() -> None:
    azar = np.random.default_rng(0)
    datos = azar.integers(0, ESCALA_SIFT + 1, (2000, DIMENSION)).astype(np.float32)
    centroides = azar.integers(0, ESCALA_SIFT + 1, (32, DIMENSION)).astype(np.float32)
    assert np.array_equal(_codebook(centroides).assign(datos), _exacto(datos, centroides))


def test_la_asignacion_coincide_aunque_los_centroides_esten_casi_juntos() -> None:
    # El caso que en float32 fallaba en 2611 de 3000.
    azar = np.random.default_rng(1)
    base = azar.integers(0, ESCALA_SIFT + 1, (1, DIMENSION)).astype(np.float32)
    centroides = np.repeat(base, 8, axis=0) + azar.normal(0, 0.05, (8, DIMENSION)).astype(
        np.float32
    )
    datos = np.repeat(base, 3000, axis=0) + azar.normal(0, 0.05, (3000, DIMENSION)).astype(
        np.float32
    )
    assert np.array_equal(_codebook(centroides).assign(datos), _exacto(datos, centroides))


def test_la_asignacion_coincide_con_valores_grandes() -> None:
    # Cuanto mayor es la norma, mayor es el termino que se cancela.
    azar = np.random.default_rng(2)
    datos = (azar.random((500, DIMENSION)) * 10_000).astype(np.float32)
    centroides = (azar.random((16, DIMENSION)) * 10_000).astype(np.float32)
    assert np.array_equal(_codebook(centroides).assign(datos), _exacto(datos, centroides))


def test_el_bloqueo_no_cambia_ninguna_asignacion() -> None:
    # El asignador trabaja por bloques de 8192 para acotar la memoria; el
    # corte entre bloques no puede alterar el resultado.
    azar = np.random.default_rng(3)
    datos = azar.integers(0, ESCALA_SIFT + 1, (9000, 16)).astype(np.float32)
    centroides = azar.integers(0, ESCALA_SIFT + 1, (12, 16)).astype(np.float32)
    etiquetas = _codebook(centroides).assign(datos)
    assert np.array_equal(etiquetas[:8192], _codebook(centroides).assign(datos[:8192]))
    assert np.array_equal(etiquetas[8192:], _codebook(centroides).assign(datos[8192:]))


def test_un_descriptor_sobre_un_centroide_cae_en_el_suyo() -> None:
    azar = np.random.default_rng(4)
    centroides = azar.integers(0, ESCALA_SIFT + 1, (20, DIMENSION)).astype(np.float32)
    codebook = _codebook(centroides)
    assert np.array_equal(codebook.assign(centroides), np.arange(20))


def test_el_vocabulario_construido_asigna_como_la_resta_directa() -> None:
    # De extremo a extremo: el codebook que sale de build_codebook, no uno
    # armado a mano.
    azar = np.random.default_rng(5)
    datos = azar.integers(0, ESCALA_SIFT + 1, (1500, DIMENSION)).astype(np.float32)
    codebook = build_codebook(datos, k=24, seed=0)
    assert np.array_equal(codebook.assign(datos), _exacto(datos, codebook.centroids))
