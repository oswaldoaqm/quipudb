"""Construccion del HNSW desde Python (issue #185).

Las propiedades del grafo estan probadas en `core/tests/index/hnsw_test.cpp`.
Aqui se comprueba lo que agrega el binding: que se crea con sus parametros,
que build e insert llegan al core, que el grafo se puede inspeccionar y que se
reabre igual.
"""

from __future__ import annotations

import pytest

quipudb = pytest.importorskip(
    "quipudb_native",
    reason="los bindings no estan compilados: cmake -DQUIPUDB_BUILD_PYTHON=ON",
)
np = pytest.importorskip("numpy")


def _vectores(n: int, dim: int, semilla: int) -> np.ndarray:
    return np.random.default_rng(semilla).normal(size=(n, dim)).astype(np.float32)


def _rids(n: int) -> list:
    return [quipudb.RID(i // 100 + 1, i % 100) for i in range(n)]


def test_construye_un_grafo_valido_y_lo_reabre(tmp_path) -> None:
    ruta = tmp_path / "indice.hnsw"
    params = quipudb.HnswParams(m=6, ef_construction=40, seed=185)
    h = quipudb.Hnsw.create(ruta, 8, quipudb.VectorMetric.COSINE, params)
    h.build(_rids(1000), _vectores(1000, 8, 1).tolist())

    assert h.size == 1000
    assert h.check_invariants() == ""
    assert h.level(h.entry_point) == h.max_level
    for nodo in range(0, 1000, 50):
        for capa in range(h.level(nodo) + 1):
            assert len(h.neighbors(nodo, capa)) <= h.max_neighbors(capa)
    assert h.max_neighbors(0) == 12
    assert h.max_neighbors(1) == 6
    assert h.last_build_ms > 0
    assert h.stats().pages_written == h.page_count
    antes = [h.neighbors(n, 0) for n in range(1000)]
    h.flush()
    del h

    h = quipudb.Hnsw.open(ruta)
    assert (h.m, h.ef_construction, h.seed) == (6, 40, 185)
    assert h.metric == quipudb.VectorMetric.COSINE
    assert [h.neighbors(n, 0) for n in range(1000)] == antes
    h.insert(quipudb.RID(99, 0), _vectores(1, 8, 2)[0].tolist())
    assert h.size == 1001
    assert h.check_invariants() == ""


def test_los_niveles_siguen_m_a_la_menos_l() -> None:
    m, n = 4, 50_000
    niveles = np.array([quipudb.Hnsw.level_for(3, i, m) for i in range(n)])
    for capa in (1, 2):
        esperado = n * m**-capa
        assert abs((niveles >= capa).sum() - esperado) < 4 * esperado**0.5


def test_parametros_y_vectores_invalidos(tmp_path) -> None:
    with pytest.raises(quipudb.SchemaError):
        quipudb.Hnsw.create(
            tmp_path / "a.hnsw", 4, quipudb.VectorMetric.EUCLIDEAN, quipudb.HnswParams(m=1)
        )
    h = quipudb.Hnsw.create(tmp_path / "b.hnsw", 4, quipudb.VectorMetric.EUCLIDEAN)
    with pytest.raises(quipudb.InvalidRecord):
        h.insert(quipudb.RID(1, 0), [1.0, 2.0])
    with pytest.raises(quipudb.IoError):
        quipudb.Hnsw.open(tmp_path / "vacio.hnsw")
