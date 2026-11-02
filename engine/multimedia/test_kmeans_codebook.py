"""Guardar, releer y comparar el codebook (issue #178).

El criterio 4 pide que el vocabulario se guarde y se reutilice, porque la
consulta tiene que cuantizarse con el MISMO que el dataset. Es el error mas
peligroso de todo el pipeline: con otro codebook la palabra 7 significa otra
cosa, los histogramas salen igual de bien formados y de la dimension
correcta, y la busqueda devuelve resultados sin sentido sin avisar. La
huella que viaja con el archivo es lo unico que permite detectarlo.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from engine.multimedia.kmeans import FORMATO, Codebook, CodebookError, build_codebook

CENTROS = np.array([[0.0, 0.0], [10.0, 10.0], [0.0, 10.0]], dtype=np.float32)


def _grupos(por_grupo: int = 60, semilla: int = 7) -> np.ndarray:
    azar = np.random.default_rng(semilla)
    nubes = [c + 0.4 * azar.standard_normal((por_grupo, 2)) for c in CENTROS]
    return np.concatenate(nubes).astype(np.float32)


# ---------------------------------------------------------------------------
# Ida y vuelta
# ---------------------------------------------------------------------------


def test_el_codebook_sobrevive_a_guardarlo_y_releerlo(tmp_path: Path) -> None:
    original = build_codebook(_grupos(), k=3, seed=11)
    original.save(tmp_path / "vocabulario.npz")
    leido = Codebook.load(tmp_path / "vocabulario.npz")
    assert np.array_equal(leido.centroids, original.centroids)
    assert leido.seed == original.seed
    assert leido.identifier == original.identifier


def test_el_codebook_releido_cuantiza_igual_que_el_original(tmp_path: Path) -> None:
    # Es lo que de verdad importa: no que los bytes coincidan, sino que una
    # consulta leida de disco caiga en las mismas palabras.
    datos = _grupos()
    original = build_codebook(datos, k=3, seed=11)
    original.save(tmp_path / "vocabulario.npz")
    leido = Codebook.load(tmp_path / "vocabulario.npz")
    assert np.array_equal(leido.assign(datos), original.assign(datos))


def test_guardar_crea_las_carpetas_que_falten(tmp_path: Path) -> None:
    destino = tmp_path / "indices" / "multimedia" / "vocabulario.npz"
    build_codebook(_grupos(), k=3, seed=0).save(destino)
    assert destino.is_file()


def test_los_centroides_se_releen_en_float32(tmp_path: Path) -> None:
    build_codebook(_grupos(), k=3, seed=0).save(tmp_path / "v.npz")
    assert Codebook.load(tmp_path / "v.npz").centroids.dtype == np.float32


# ---------------------------------------------------------------------------
# Deteccion de un codebook que no corresponde
# ---------------------------------------------------------------------------


def test_dos_vocabularios_distintos_se_distinguen_por_la_huella() -> None:
    # El escenario del issue: el dataset se indexo con uno y la consulta se
    # cuantiza con otro. Sin la huella, nadie se entera.
    datos = _grupos(por_grupo=300)
    dataset = build_codebook(datos, k=3, seed=1, max_descriptors=100)
    consulta = build_codebook(datos, k=3, seed=2, max_descriptors=100)
    assert dataset.identifier != consulta.identifier


def test_un_codebook_alterado_a_mano_se_rechaza(tmp_path: Path) -> None:
    ruta = tmp_path / "vocabulario.npz"
    original = build_codebook(_grupos(), k=3, seed=0)
    original.save(ruta)
    # Se reescribe con otros centroides pero la huella vieja.
    movidos = original.centroids + 1.0
    with ruta.open("wb") as salida:
        np.savez(
            salida,
            formato=np.asarray(FORMATO),
            centroids=movidos,
            seed=np.asarray(original.seed),
            identifier=np.asarray(original.identifier),
        )
    with pytest.raises(CodebookError, match="no coincide con su huella"):
        Codebook.load(ruta)


def test_un_codebook_de_otro_formato_se_rechaza(tmp_path: Path) -> None:
    # Que la version suba es lo que evita leer mal un archivo viejo en vez
    # de decir que no se puede.
    ruta = tmp_path / "vocabulario.npz"
    original = build_codebook(_grupos(), k=3, seed=0)
    with ruta.open("wb") as salida:
        np.savez(
            salida,
            formato=np.asarray(FORMATO + 1),
            centroids=original.centroids,
            seed=np.asarray(original.seed),
            identifier=np.asarray(original.identifier),
        )
    with pytest.raises(CodebookError, match="formato"):
        Codebook.load(ruta)


# ---------------------------------------------------------------------------
# Archivos que no se pueden leer
# ---------------------------------------------------------------------------


def test_un_codebook_que_no_existe_da_un_error_claro(tmp_path: Path) -> None:
    with pytest.raises(CodebookError, match="no se puede leer"):
        Codebook.load(tmp_path / "fantasma.npz")


def test_un_archivo_que_no_es_un_codebook_da_un_error_claro(tmp_path: Path) -> None:
    ruta = tmp_path / "notas.npz"
    ruta.write_text("esto no es un codebook", encoding="utf-8")
    with pytest.raises(CodebookError, match="no se puede leer"):
        Codebook.load(ruta)


def test_un_npz_sin_los_campos_del_codebook_da_un_error_claro(tmp_path: Path) -> None:
    ruta = tmp_path / "otro.npz"
    with ruta.open("wb") as salida:
        np.savez(salida, cualquier_cosa=np.arange(3))
    with pytest.raises(CodebookError, match="no se puede leer"):
        Codebook.load(ruta)


# ---------------------------------------------------------------------------
# Cuantizar
# ---------------------------------------------------------------------------


def test_cuantizar_con_una_dimension_equivocada_da_un_error_claro() -> None:
    # Pasarle descriptores MFCC de 13 valores a un codebook de SIFT de 128
    # es un error de cableado, no de datos, y tiene que doler enseguida.
    codebook = build_codebook(_grupos(), k=3, seed=0)
    with pytest.raises(CodebookError, match="dimension"):
        codebook.assign(np.zeros((5, 13), dtype=np.float32))


def test_cuantizar_devuelve_un_indice_por_descriptor() -> None:
    codebook = build_codebook(_grupos(), k=3, seed=0)
    etiquetas = codebook.assign(np.zeros((17, 2), dtype=np.float32))
    assert etiquetas.shape == (17,)
    assert set(etiquetas.tolist()) <= set(range(3))


def test_cuantizar_funciona_con_mas_descriptores_que_un_bloque() -> None:
    # El asignador trabaja por bloques para acotar la memoria; con mas datos
    # que un bloque tiene que dar lo mismo que de una sola vez.
    codebook = build_codebook(_grupos(), k=3, seed=0)
    muchos = np.tile(_grupos(), (60, 1))
    assert len(codebook.assign(muchos)) == len(muchos)
