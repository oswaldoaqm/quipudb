"""Construccion del IVF con los centroides del K-Means compartido (issue #183).

El criterio 2 del issue pide que los centroides salgan del mismo K-Means que
arma el codebook de SIFT y MFCC (#178). Esta prueba hace justo ese camino:
entrena `build_codebook` sobre los vectores, crea el IVF con sus centroides y
comprueba que cada lista tiene los vectores que el codebook asigna a ese
centroide.
"""

from __future__ import annotations

import pytest

quipudb = pytest.importorskip(
    "quipudb_native",
    reason="los bindings no estan compilados: cmake -DQUIPUDB_BUILD_PYTHON=ON",
)
np = pytest.importorskip("numpy")

from engine.multimedia.kmeans import build_codebook  # noqa: E402


def _agrupados(n: int, dim: int, grupos: int, semilla: int) -> np.ndarray:
    azar = np.random.default_rng(semilla)
    centros = azar.normal(0.0, 10.0, size=(grupos, dim))
    cual = azar.integers(0, grupos, size=n)
    return (centros[cual] + azar.normal(0.0, 0.5, size=(n, dim))).astype(np.float32)


def test_las_listas_son_las_celdas_del_kmeans(tmp_path) -> None:
    vectores = _agrupados(n=600, dim=8, grupos=6, semilla=183)
    codebook = build_codebook(vectores, 6, seed=7)
    rids = [quipudb.RID(i // 100 + 1, i % 100) for i in range(len(vectores))]

    ivf = quipudb.Ivf.create(tmp_path / "indice.ivf", codebook.centroids.tolist())
    ivf.build(rids, vectores.tolist())

    assert ivf.nlist == 6
    assert ivf.size == 600
    assert ivf.check_invariants() == ""
    asignacion = codebook.assign(vectores)
    for lista in range(6):
        esperados = {(r.page, r.slot) for r, a in zip(rids, asignacion, strict=True) if a == lista}
        obtenidos = {(r.page, r.slot) for r, _v in ivf.list_entries(lista)}
        assert obtenidos == esperados, f"lista {lista}"
    assert ivf.last_build_ms > 0


def test_se_reabre_e_inserta(tmp_path) -> None:
    ruta = tmp_path / "indice.ivf"
    ivf = quipudb.Ivf.create(ruta, [[0.0, 0.0], [10.0, 10.0]])
    ivf.insert(quipudb.RID(1, 0), [9.0, 11.0])
    ivf.flush()
    del ivf

    ivf = quipudb.Ivf.open(ruta)
    assert ivf.size == 1
    assert ivf.nearest_list([1.0, -1.0]) == 0
    [(rid, vector)] = ivf.list_entries(1)
    assert (rid.page, rid.slot) == (1, 0)
    assert vector == pytest.approx([9.0, 11.0])


def test_un_vector_de_otra_dimension_es_invalid_record(tmp_path) -> None:
    ivf = quipudb.Ivf.create(tmp_path / "indice.ivf", [[0.0, 0.0, 0.0]])
    with pytest.raises(quipudb.InvalidRecord):
        ivf.insert(quipudb.RID(1, 0), [1.0, 2.0])
    with pytest.raises(quipudb.InvalidRecord):
        ivf.build([quipudb.RID(1, 0)], [[1.0, 2.0]])
