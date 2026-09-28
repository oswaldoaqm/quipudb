"""Pruebas E2E de DISTANCIA y el R-Tree a traves de los bindings."""

import pytest

from engine.executor import QueryProcessor

quipudb = pytest.importorskip(
    "quipudb_native",
    reason="los bindings no estan compilados: cmake -DQUIPUDB_BUILD_PYTHON=ON",
)

_CENTER = "POINT(-12.0464, -77.0428)"


def _processor(tmp_path):
    database = quipudb.Database(tmp_path / "catalogo.txt")
    processor = QueryProcessor(database)
    processor.execute(
        "CREATE TABLE tiendas ("
        "id INT PRIMARY KEY, nombre VARCHAR(20), ubicacion POINT"
        ") USING HEAP"
    )
    processor.execute(f"INSERT INTO tiendas VALUES (1, 'centro', {_CENTER})")
    processor.execute(
        "INSERT INTO tiendas VALUES (2, 'cerca', POINT(-12.05, -77.04))"
    )
    processor.execute(
        "INSERT INTO tiendas VALUES (3, 'arequipa', POINT(-16.3989, -71.5375))"
    )
    return database, processor


def _operations(result) -> list[tuple[str, str]]:
    assert result.plan is not None
    return [(step.op.value, step.structure.value) for step in result.plan.root.walk()]


def test_distancia_sin_indice_hace_scan_mas_filtro(tmp_path) -> None:
    _, processor = _processor(tmp_path)

    result = processor.execute(
        f"SELECT id, nombre FROM tiendas WHERE distancia(ubicacion, {_CENTER}) < 5000"
    )

    assert set(result.rows) == {(1, "centro"), (2, "cerca")}
    assert _operations(result) == [
        ("scan", "heap"),
        ("filter", "memory"),
        ("project", "memory"),
    ]


def test_distancia_usa_rtree_y_expone_la_metrica_elegida(tmp_path) -> None:
    _, processor = _processor(tmp_path)
    processor.execute(
        "CREATE INDEX tiendas_ubicacion ON tiendas (ubicacion) USING RTREE"
    )

    result = processor.execute(
        f"SELECT id FROM tiendas WHERE "
        f"distancia(ubicacion, {_CENTER}, EUCLIDEAN) <= 0.01"
    )

    assert set(result.rows) == {(1,), (2,)}
    assert _operations(result) == [
        ("radius_search", "rtree"),
        ("fetch", "heap"),
        ("project", "memory"),
    ]
    assert result.plan is not None
    radius = result.plan.root.walk()[0]
    assert "EUCLIDEAN" in radius.detail
    assert radius.stats.records_returned == 2


def test_rtree_se_mantiene_con_insert_delete_y_reapertura(tmp_path) -> None:
    database, processor = _processor(tmp_path)
    processor.execute(
        "CREATE INDEX tiendas_ubicacion ON tiendas (ubicacion) USING RTREE"
    )
    processor.execute(
        "INSERT INTO tiendas VALUES (4, 'nuevo', POINT(-12.047, -77.043))"
    )
    processor.execute("DELETE FROM tiendas WHERE id = 2")

    source = f"SELECT id FROM tiendas WHERE distancia(ubicacion, {_CENTER}) <= 5000"
    result = processor.execute(source)

    assert set(result.rows) == {(1,), (4,)}
    assert _operations(result)[0] == ("radius_search", "rtree")

    database.flush()
    database.close("tiendas")
    reopened = QueryProcessor(quipudb.Database(tmp_path / "catalogo.txt"))
    persisted = reopened.execute(source)
    assert set(persisted.rows) == {(1,), (4,)}
    assert _operations(persisted)[0] == ("radius_search", "rtree")
