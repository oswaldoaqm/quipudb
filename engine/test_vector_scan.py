"""La busqueda k-NN exacta vista desde Python (issue #182).

Los resultados estan probados en `core/tests/index/vector_scan_test.cpp`. Aqui
se comprueba lo que agrega el binding: que recibe la tabla del catalogo, que
devuelve RID y distancia, y que los errores llegan con su tipo.
"""

from __future__ import annotations

import pytest

quipudb = pytest.importorskip(
    "quipudb_native",
    reason="los bindings no estan compilados: cmake -DQUIPUDB_BUILD_PYTHON=ON",
)


@pytest.fixture()
def tabla(tmp_path):
    db = quipudb.Database(tmp_path / "catalogo.txt")
    esquema = quipudb.Schema(
        table_name="fotos",
        columns=[
            quipudb.Column("id", quipudb.DataType.INT),
            quipudb.Column("ruta", quipudb.DataType.VARCHAR, 8),
            quipudb.Column("rasgos", quipudb.DataType.VECTOR, 2),
        ],
        key_column=0,
    )
    t = db.create_table(esquema, quipudb.kind.HEAP)
    rids = [
        t.insert([1, "a", [1.0, 0.0]]),
        t.insert([2, "b", [0.0, 1.0]]),
        t.insert([3, "c", [3.0, 0.0]]),
    ]
    return db, t, rids


def test_devuelve_rid_y_distancia_de_mas_cerca_a_mas_lejos(tabla) -> None:
    _db, t, (a, b, c) = tabla
    r = quipudb.scan_k_nearest_vector(t, "rasgos", [2.0, 0.0], 3, quipudb.VectorMetric.EUCLIDEAN)
    assert [(m.rid.page, m.rid.slot) for m in r] == [
        (a.page, a.slot),
        (c.page, c.slot),
        (b.page, b.slot),
    ]
    assert [m.distance for m in r] == pytest.approx([1.0, 1.0, 5**0.5])


def test_la_distancia_se_convierte_al_valor_de_la_metrica(tabla) -> None:
    _db, t, _rids = tabla
    metrica = quipudb.VectorMetric.DOT_PRODUCT
    mejor = quipudb.scan_k_nearest_vector(t, "rasgos", [2.0, 0.0], 1, metrica)[0]
    assert quipudb.from_distance(mejor.distance, metrica) == pytest.approx(6.0)


def test_una_consulta_de_otra_dimension_es_invalid_record(tabla) -> None:
    _db, t, _rids = tabla
    with pytest.raises(quipudb.InvalidRecord, match=r"VECTOR\(2\)"):
        quipudb.scan_k_nearest_vector(t, "rasgos", [1.0, 2.0, 3.0], 1, quipudb.VectorMetric.COSINE)


def test_una_columna_que_no_es_vector_es_schema_error(tabla) -> None:
    _db, t, _rids = tabla
    with pytest.raises(quipudb.SchemaError):
        quipudb.scan_k_nearest_vector(t, "ruta", [1.0, 2.0], 1, quipudb.VectorMetric.COSINE)
