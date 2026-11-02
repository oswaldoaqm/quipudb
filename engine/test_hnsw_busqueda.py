"""Busqueda k-NN del HNSW con ef_search, desde Python (issue #186).

Lo que se comprueba es lo que dice la teoria: con ef_search >= N el resultado
es el mismo que el del scan exacto (#182), y con menos candidatos se comparan
muchos menos vectores que N mientras el Recall@10 sube con ef_search.
"""

from __future__ import annotations

from itertools import pairwise

import pytest

quipudb = pytest.importorskip(
    "quipudb_native",
    reason="los bindings no estan compilados: cmake -DQUIPUDB_BUILD_PYTHON=ON",
)
np = pytest.importorskip("numpy")

DIM = 8


def _base(tmp_path, n: int, metrica, semilla: int = 1):
    db = quipudb.Database(tmp_path / "catalogo.txt")
    esquema = quipudb.Schema(
        table_name="imagenes",
        columns=[
            quipudb.Column("id", quipudb.DataType.INT),
            quipudb.Column("vec", quipudb.DataType.VECTOR, DIM),
        ],
        key_column=0,
    )
    tabla = db.create_table(esquema, quipudb.kind.HEAP)
    vectores = np.random.default_rng(semilla).normal(size=(n, DIM)).astype(np.float32)
    for i, v in enumerate(vectores):
        tabla.insert([i, v.tolist()])
    params = quipudb.HnswParams(m=8, ef_construction=64, seed=186)
    indice = db.create_hnsw_index("imagenes", "por_vec", "vec", metrica, params)
    return db, tabla, indice


def _rids(matches) -> list[tuple[int, int]]:
    return [(m.rid.page, m.rid.slot) for m in matches]


@pytest.mark.parametrize(
    "metrica",
    [
        quipudb.VectorMetric.EUCLIDEAN,
        quipudb.VectorMetric.DOT_PRODUCT,
        quipudb.VectorMetric.COSINE,
    ],
)
def test_con_ef_search_grande_es_la_busqueda_exacta(tmp_path, metrica) -> None:
    _db, tabla, indice = _base(tmp_path, 600, metrica)
    assert isinstance(indice, quipudb.HnswIndex)
    assert indice.kind == quipudb.kind.HNSW
    assert indice.size() == 600
    consultas = np.random.default_rng(2).normal(size=(15, DIM)).astype(np.float32)
    for q in consultas:
        exacto = quipudb.scan_k_nearest_vector(tabla, "vec", q.tolist(), 10, metrica)
        hnsw = indice.k_nearest(q.tolist(), 10, metrica, 600)
        assert _rids(hnsw) == _rids(exacto)
        assert [m.distance for m in hnsw] == pytest.approx([m.distance for m in exacto])


def test_el_recall_sube_con_ef_search_y_compara_menos_que_n(tmp_path) -> None:
    n = 3000
    metrica = quipudb.VectorMetric.EUCLIDEAN
    _db, tabla, indice = _base(tmp_path, n, metrica, semilla=3)
    consultas = np.random.default_rng(4).normal(size=(40, DIM)).astype(np.float32)
    verdad = [
        set(_rids(quipudb.scan_k_nearest_vector(tabla, "vec", q.tolist(), 10, metrica)))
        for q in consultas
    ]

    recalls, comparados = [], []
    for ef in (10, 20, 40, 80, 160):
        aciertos, examinados = 0, 0
        for q, esperado in zip(consultas, verdad, strict=True):
            indice.reset_stats()
            obtenido = set(_rids(indice.k_nearest(q.tolist(), 10, metrica, ef)))
            aciertos += len(obtenido & esperado)
            examinados += indice.stats().records_examined
        recalls.append(aciertos / (10 * len(consultas)))
        comparados.append(examinados / len(consultas))

    # En promedio sube; consulta por consulta no esta garantizado.
    for antes, despues in pairwise(recalls):
        assert despues >= antes - 0.01
    assert comparados == sorted(comparados)
    assert recalls[-1] >= 0.99
    assert comparados[-1] < n / 2


def test_el_executor_puede_mantenerlo_al_borrar(tmp_path) -> None:
    metrica = quipudb.VectorMetric.COSINE
    _db, _tabla, indice = _base(tmp_path, 200, metrica, semilla=5)
    [(vector, rid)] = indice.scan()[17:18]
    assert indice.remove_one(vector, rid)
    assert not indice.remove_one(vector, rid)
    assert indice.size() == 199
    assert indice.hnsw.size == 200
    resultado = indice.k_nearest(vector, 5, metrica, 200)
    assert (rid.page, rid.slot) not in _rids(resultado)
    with pytest.raises(quipudb.Unsupported):
        indice.range_search(vector, vector)


def test_la_metrica_tiene_que_ser_la_del_grafo(tmp_path) -> None:
    _db, _tabla, indice = _base(tmp_path, 50, quipudb.VectorMetric.COSINE)
    with pytest.raises(quipudb.SchemaError, match="COSINE"):
        indice.k_nearest([1.0] * DIM, 5, quipudb.VectorMetric.DOT_PRODUCT, 10)
    with pytest.raises(quipudb.InvalidRecord):
        indice.k_nearest([1.0] * DIM, 5, quipudb.VectorMetric.COSINE, 0)


def test_create_index_sin_metrica_lo_rechaza(tmp_path) -> None:
    db, _tabla, _indice = _base(tmp_path, 20, quipudb.VectorMetric.EUCLIDEAN)
    with pytest.raises(quipudb.SchemaError, match="create_hnsw_index"):
        db.create_index("imagenes", "otro", "vec", quipudb.kind.HNSW)
