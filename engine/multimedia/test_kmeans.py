"""K-Means sobre grupos sinteticos de centros conocidos (issue #178).

Es el criterio 6 del issue, y la unica forma honesta de probar un algoritmo
de agrupamiento: se fabrican los grupos con centros que uno eligio y se
comprueba que los recupera. Sobre datos reales no hay respuesta correcta
contra la cual comparar, asi que una prueba solo diria que el codigo corre.

No necesitan el core ni soundfile ni OpenCV: este modulo trabaja sobre una
matriz de N x D cualquiera.
"""

from __future__ import annotations

import numpy as np

from engine.multimedia.kmeans import build_codebook

CENTROS = np.array([[0.0, 0.0], [10.0, 10.0], [0.0, 10.0]], dtype=np.float32)


def _grupos(por_grupo: int = 60, dispersion: float = 0.4, semilla: int = 7) -> np.ndarray:
    """Nubes apretadas alrededor de CENTROS, bien separadas entre si."""

    azar = np.random.default_rng(semilla)
    nubes = [c + dispersion * azar.standard_normal((por_grupo, 2)) for c in CENTROS]
    return np.concatenate(nubes).astype(np.float32)


def _emparejar(encontrados: np.ndarray) -> np.ndarray:
    """Distancia de cada centro verdadero al centroide mas cercano."""

    return np.array(
        [np.min(np.linalg.norm(encontrados - centro, axis=1)) for centro in CENTROS]
    )


def test_recupera_los_centros_que_se_usaron_para_generar_los_grupos() -> None:
    codebook = build_codebook(_grupos(), k=3, seed=0)
    # Holgado a proposito: la dispersion es 0.4, asi que el promedio de cada
    # nube no cae exactamente en su centro.
    assert (_emparejar(codebook.centroids) < 0.5).all()


def test_el_codebook_tiene_k_palabras_de_la_dimension_de_los_datos() -> None:
    codebook = build_codebook(_grupos(), k=3, seed=0)
    assert codebook.k == 3
    assert codebook.dim == 2
    assert codebook.centroids.shape == (3, 2)


def test_los_centroides_quedan_en_float32() -> None:
    # Los descriptores de SIFT y MFCC llegan en float32; duplicar la memoria
    # del vocabulario sin motivo seria caro con K grande.
    assert build_codebook(_grupos(), k=3, seed=0).centroids.dtype == np.float32


def test_cada_punto_se_asigna_al_centroide_de_su_propio_grupo() -> None:
    datos = _grupos(por_grupo=60)
    codebook = build_codebook(datos, k=3, seed=0)
    etiquetas = codebook.assign(datos)
    # Los primeros 60 son del primer grupo, los siguientes 60 del segundo, etc.
    for grupo in range(3):
        tramo = etiquetas[grupo * 60 : (grupo + 1) * 60]
        assert len(set(tramo.tolist())) == 1, "un grupo quedo partido entre dos palabras"
    assert len(set(etiquetas.tolist())) == 3, "dos grupos cayeron en la misma palabra"


def test_un_punto_sobre_un_centro_cae_en_su_palabra() -> None:
    datos = _grupos()
    codebook = build_codebook(datos, k=3, seed=0)
    for centro in CENTROS:
        palabra = codebook.assign(centro[None, :])[0]
        assert np.linalg.norm(codebook.centroids[palabra] - centro) < 0.5


def test_ningun_centroide_se_queda_sin_puntos() -> None:
    # Con mas palabras que grupos naturales, un centroide puede quedarse
    # vacio en la primera vuelta. Dejarlo quieto lo congela para siempre y el
    # vocabulario pierde una palabra en silencio; por eso se recoloca.
    codebook = build_codebook(_grupos(), k=6, seed=0)
    cuentas = np.bincount(codebook.assign(_grupos()), minlength=6)
    assert (cuentas > 0).all()


def test_las_palabras_del_vocabulario_son_distintas_entre_si() -> None:
    codebook = build_codebook(_grupos(), k=6, seed=0)
    assert len(np.unique(codebook.centroids, axis=0)) == 6


def test_mas_iteraciones_no_cambian_un_resultado_ya_convergido() -> None:
    pocas = build_codebook(_grupos(), k=3, seed=0, max_iterations=50)
    muchas = build_codebook(_grupos(), k=3, seed=0, max_iterations=200)
    assert np.allclose(pocas.centroids, muchas.centroids)


def test_un_solo_centroide_cae_en_el_promedio_de_todo() -> None:
    datos = _grupos()
    codebook = build_codebook(datos, k=1, seed=0)
    assert np.allclose(codebook.centroids[0], datos.mean(axis=0), atol=1e-3)
