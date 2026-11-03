"""Busqueda k-NN del IVF con nprobe, desde Python (issue #184).

Lo que se comprueba es lo que dice la teoria del IVF: con nprobe = nlist se
visitan todas las listas y el resultado es el mismo que el del scan exacto
(#182); con menos listas se comparan menos vectores, mas o menos
N * nprobe / nlist, y el Recall@10 nunca baja al subir nprobe.

Los centroides salen del K-Means compartido (#178), igual que en el #183.
"""

from __future__ import annotations

import pytest

pytest.importorskip("numpy")

from engine.multimedia.kmeans import build_codebook

quipudb = pytest.importorskip(
    "quipudb_native",
    reason="los bindings no estan compilados: cmake -DQUIPUDB_BUILD_PYTHON=ON",
)
np = pytest.importorskip("numpy")

DIM = 8
NLIST = 16


def _agrupados(n: int, grupos: int, semilla: int) -> np.ndarray:
    azar = np.random.default_rng(semilla)
    centros = azar.normal(0.0, 3.0, size=(grupos, DIM))
    cual = azar.integers(0, grupos, size=n)
    return (centros[cual] + azar.normal(0.0, 1.5, size=(n, DIM))).astype(np.float32)


def _base(tmp_path, vectores: np.ndarray):
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
    for i, v in enumerate(vectores):
        tabla.insert([i, v.tolist()])
    codebook = build_codebook(vectores, NLIST, seed=184)
    indice = db.create_ivf_index("imagenes", "por_vec", "vec", codebook.centroids.tolist())
    return db, tabla, indice


def _rids(matches) -> list[tuple[int, int]]:
    return [(m.rid.page, m.rid.slot) for m in matches]


def test_con_nprobe_igual_a_nlist_es_la_busqueda_exacta(tmp_path) -> None:
    vectores = _agrupados(800, grupos=10, semilla=1)
    _db, tabla, indice = _base(tmp_path, vectores)
    assert isinstance(indice, quipudb.IvfIndex)
    assert indice.kind == quipudb.kind.IVF
    assert indice.size() == 800

    consultas = _agrupados(20, grupos=10, semilla=2)
    for metrica in (
        quipudb.VectorMetric.EUCLIDEAN,
        quipudb.VectorMetric.DOT_PRODUCT,
        quipudb.VectorMetric.COSINE,
    ):
        for q in consultas:
            exacto = quipudb.scan_k_nearest_vector(tabla, "vec", q.tolist(), 10, metrica)
            ivf = indice.k_nearest(q.tolist(), 10, metrica, NLIST)
            assert _rids(ivf) == _rids(exacto)
            assert [m.distance for m in ivf] == pytest.approx([m.distance for m in exacto])


def test_el_recall_sube_con_nprobe_y_las_comparaciones_tambien(tmp_path) -> None:
    n = 3000
    vectores = _agrupados(n, grupos=NLIST, semilla=3)
    _db, tabla, indice = _base(tmp_path, vectores)
    consultas = _agrupados(40, grupos=NLIST, semilla=4)
    metrica = quipudb.VectorMetric.EUCLIDEAN
    verdad = [
        set(_rids(quipudb.scan_k_nearest_vector(tabla, "vec", q.tolist(), 10, metrica)))
        for q in consultas
    ]

    recalls, comparados = [], []
    for nprobe in (1, 2, 4, 8, 16):
        aciertos, examinados = 0, 0
        for q, esperado in zip(consultas, verdad, strict=True):
            indice.reset_stats()
            obtenido = set(_rids(indice.k_nearest(q.tolist(), 10, metrica, nprobe)))
            aciertos += len(obtenido & esperado)
            examinados += indice.stats().records_examined
        recalls.append(aciertos / (10 * len(consultas)))
        comparados.append(examinados / len(consultas))

    assert recalls == sorted(recalls)
    assert comparados == sorted(comparados)
    assert recalls[-1] == 1.0
    assert comparados[-1] == n
    # Con una sola lista se mira cerca de N / nlist, no la tabla entera.
    assert comparados[0] < n / 4


def test_el_executor_puede_mantenerlo_al_borrar(tmp_path) -> None:
    vectores = _agrupados(200, grupos=4, semilla=5)
    _db, _tabla, indice = _base(tmp_path, vectores)
    objetivo = vectores[17].tolist()
    assert len(indice.search(objetivo)) == 1
    assert indice.remove(objetivo) == 1
    assert indice.search(objetivo) == []
    assert indice.size() == 199
    assert indice.ivf.check_invariants() == ""
    with pytest.raises(quipudb.Unsupported):
        indice.range_search(objetivo, objetivo)


def test_create_index_sin_centroides_lo_rechaza(tmp_path) -> None:
    db, _tabla, _indice = _base(tmp_path, _agrupados(50, grupos=2, semilla=6))
    with pytest.raises(quipudb.SchemaError, match="create_ivf_index"):
        db.create_index("imagenes", "otro", "vec", quipudb.kind.IVF)
    with pytest.raises(quipudb.SchemaError):
        db.create_index("imagenes", "hash_vec", "vec", quipudb.kind.EXTENDIBLE_HASH)


def test_ivf_suelto_busca_y_borra(tmp_path) -> None:
    ivf = quipudb.Ivf.create(tmp_path / "suelto.ivf", [[0.0, 0.0], [10.0, 10.0]])
    ivf.insert(quipudb.RID(1, 0), [1.0, 1.0])
    ivf.insert(quipudb.RID(1, 1), [9.0, 9.0])
    assert ivf.probe_order([8.0, 8.0]) == [1, 0]
    [cercano] = ivf.search([8.0, 8.0], 1, quipudb.VectorMetric.EUCLIDEAN, 1)
    assert (cercano.rid.page, cercano.rid.slot) == (1, 1)
    assert ivf.remove(quipudb.RID(1, 1), [9.0, 9.0])
    assert not ivf.remove(quipudb.RID(1, 1), [9.0, 9.0])
    assert ivf.search([8.0, 8.0], 1, quipudb.VectorMetric.EUCLIDEAN, 1) == []
    with pytest.raises(quipudb.InvalidRecord):
        ivf.search([8.0, 8.0], 1, quipudb.VectorMetric.EUCLIDEAN, 0)
