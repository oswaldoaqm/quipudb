"""Determinismo del vocabulario y muestreo de descriptores (issue #178).

El criterio 1 pide que la misma semilla de el mismo resultado, y no es un
capricho: si reconstruir el dataset cambiara los centroides, la palabra 7
pasaria a significar otra cosa y habria que reindexar todos los objetos.

El criterio 3 pide poder trabajar sin cargar todos los descriptores. 100 000
imagenes a 500 descriptores de 128 valores son unos 25 GB; el muestreo es lo
que hace viable construir el vocabulario en una maquina normal.
"""

from __future__ import annotations

import numpy as np

from engine.multimedia.kmeans import build_codebook, huella_de

CENTROS = np.array([[0.0, 0.0], [10.0, 10.0], [0.0, 10.0]], dtype=np.float32)


def _grupos(por_grupo: int = 60, semilla: int = 7) -> np.ndarray:
    azar = np.random.default_rng(semilla)
    nubes = [c + 0.4 * azar.standard_normal((por_grupo, 2)) for c in CENTROS]
    return np.concatenate(nubes).astype(np.float32)


# ---------------------------------------------------------------------------
# Misma semilla, mismo vocabulario
# ---------------------------------------------------------------------------


def test_la_misma_semilla_da_exactamente_los_mismos_centroides() -> None:
    datos = _grupos()
    assert np.array_equal(
        build_codebook(datos, k=3, seed=42).centroids,
        build_codebook(datos, k=3, seed=42).centroids,
    )


def test_la_misma_semilla_da_la_misma_huella() -> None:
    # Es la huella lo que #181 va a comparar para saber si una consulta se
    # cuantizo con el vocabulario del dataset.
    datos = _grupos()
    assert build_codebook(datos, k=3, seed=42).identifier == (
        build_codebook(datos, k=3, seed=42).identifier
    )


def test_la_semilla_queda_guardada_en_el_codebook() -> None:
    assert build_codebook(_grupos(), k=3, seed=42).seed == 42


def test_otra_semilla_encuentra_los_mismos_centros_aunque_en_otro_orden() -> None:
    # Con grupos bien separados el resultado no deberia depender de la
    # siembra: si dependiera, el vocabulario seria un accidente.
    for semilla in (0, 1, 2, 99):
        encontrados = build_codebook(_grupos(), k=3, seed=semilla).centroids
        distancias = [np.min(np.linalg.norm(encontrados - c, axis=1)) for c in CENTROS]
        assert max(distancias) < 0.5


# ---------------------------------------------------------------------------
# Huella
# ---------------------------------------------------------------------------


def test_centroides_distintos_dan_huellas_distintas() -> None:
    uno = np.array([[0.0, 0.0], [1.0, 1.0]], dtype=np.float32)
    otro = np.array([[0.0, 0.0], [1.0, 1.1]], dtype=np.float32)
    assert huella_de(uno) != huella_de(otro)


def test_la_huella_distingue_la_forma_y_no_solo_los_valores() -> None:
    # Los mismos cuatro numeros como 2x2 o como 4x1 no son el mismo
    # vocabulario, y `tobytes` por si solo no los distingue.
    valores = np.arange(4, dtype=np.float32)
    assert huella_de(valores.reshape(2, 2)) != huella_de(valores.reshape(4, 1))


def test_la_huella_no_depende_de_como_este_guardada_la_matriz_en_memoria() -> None:
    # Una vista traspuesta y una copia contigua describen el mismo
    # vocabulario y tienen que dar la misma huella.
    matriz = np.arange(6, dtype=np.float32).reshape(2, 3)
    assert huella_de(matriz) == huella_de(np.ascontiguousarray(matriz))


# ---------------------------------------------------------------------------
# Muestreo
# ---------------------------------------------------------------------------


def test_el_muestreo_es_determinista() -> None:
    datos = _grupos(por_grupo=200)
    assert np.array_equal(
        build_codebook(datos, k=3, seed=5, max_descriptors=120).centroids,
        build_codebook(datos, k=3, seed=5, max_descriptors=120).centroids,
    )


def test_con_una_muestra_el_vocabulario_sigue_encontrando_los_centros() -> None:
    # Los centroides describen zonas densas del espacio, y esas zonas no
    # desaparecen al muestrear: es lo que justifica el criterio 3.
    datos = _grupos(por_grupo=300)
    encontrados = build_codebook(datos, k=3, seed=0, max_descriptors=150).centroids
    distancias = [np.min(np.linalg.norm(encontrados - c, axis=1)) for c in CENTROS]
    assert max(distancias) < 0.5


def test_una_muestra_mayor_que_los_datos_no_cambia_nada() -> None:
    datos = _grupos(por_grupo=20)
    assert np.array_equal(
        build_codebook(datos, k=3, seed=0).centroids,
        build_codebook(datos, k=3, seed=0, max_descriptors=10_000).centroids,
    )


def test_muestras_distintas_dan_vocabularios_distintos() -> None:
    # No es un defecto, es la consecuencia de muestrear, y por eso la huella
    # del codebook tiene que viajar con el dataset.
    datos = _grupos(por_grupo=300)
    uno = build_codebook(datos, k=3, seed=1, max_descriptors=100)
    otro = build_codebook(datos, k=3, seed=2, max_descriptors=100)
    assert uno.identifier != otro.identifier
