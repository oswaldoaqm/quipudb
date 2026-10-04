"""Pruebas E2E de DENTRO(columna, POLYGON(...)) a traves de los bindings."""

import pytest

from engine.executor import QueryProcessor

quipudb = pytest.importorskip(
    "quipudb_native",
    reason="los bindings no estan compilados: cmake -DQUIPUDB_BUILD_PYTHON=ON",
)

# Un "distrito" en forma de L alrededor del centro de Lima.
_DISTRITO = (
    "POLYGON(POINT(-12.20, -77.20), POINT(-12.20, -76.90), POINT(-12.10, -76.90), "
    "POINT(-12.10, -77.05), POINT(-11.90, -77.05), POINT(-11.90, -77.20))"
)
_TIENDAS = [
    (1, "brazo sur", -12.15, -77.10),
    (2, "brazo oeste", -12.00, -77.10),
    (3, "hueco", -12.00, -76.95),  # dentro de la caja, fuera del poligono
    (4, "arista", -12.20, -77.00),
    (5, "vertice", -12.10, -77.05),
    (6, "arequipa", -16.3989, -71.5375),
]
_DENTRO = {1, 2, 4, 5}


def _processor(tmp_path, *, indexed: bool):
    database = quipudb.Database(tmp_path / "catalogo.txt")
    processor = QueryProcessor(database)
    processor.execute(
        "CREATE TABLE tiendas (id INT PRIMARY KEY, nombre VARCHAR(20), ubicacion POINT) "
        "USING HEAP"
    )
    for id_, nombre, lat, lon in _TIENDAS:
        processor.execute(f"INSERT INTO tiendas VALUES ({id_}, '{nombre}', POINT({lat}, {lon}))")
    if indexed:
        processor.execute("CREATE INDEX tiendas_geo ON tiendas (ubicacion) USING RTREE")
    return database, processor


def _operations(result) -> list[tuple[str, str]]:
    assert result.plan is not None
    return [(step.op.value, step.structure.value) for step in result.plan.root.walk()]


def test_dentro_con_rtree_usa_polygon_search_y_bordes_incluidos(tmp_path) -> None:
    _, processor = _processor(tmp_path, indexed=True)

    result = processor.execute(f"SELECT id FROM tiendas WHERE DENTRO(ubicacion, {_DISTRITO})")

    assert {row[0] for row in result.rows} == _DENTRO
    assert _operations(result) == [
        ("polygon_search", "rtree"),
        ("fetch", "heap"),
        ("project", "memory"),
    ]
    search = result.plan.root.walk()[0]
    # La caja deja pasar el "hueco"; el poligono lo descarta.
    assert search.stats.records_returned == len(_DENTRO)
    assert search.stats.records_examined > search.stats.records_returned


def test_dentro_sin_indice_hace_scan_mas_filtro_con_el_mismo_resultado(tmp_path) -> None:
    _, processor = _processor(tmp_path, indexed=False)

    result = processor.execute(f"SELECT id FROM tiendas WHERE DENTRO(ubicacion, {_DISTRITO})")

    assert {row[0] for row in result.rows} == _DENTRO
    assert _operations(result) == [
        ("scan", "heap"),
        ("filter", "memory"),
        ("project", "memory"),
    ]


def test_dentro_se_combina_con_order_by_y_limit(tmp_path) -> None:
    _, processor = _processor(tmp_path, indexed=True)

    result = processor.execute(
        f"SELECT id FROM tiendas WHERE DENTRO(ubicacion, {_DISTRITO}) ORDER BY id DESC LIMIT 2"
    )

    assert result.rows == ((5,), (4,))


def test_delete_con_dentro_mantiene_el_rtree(tmp_path) -> None:
    _, processor = _processor(tmp_path, indexed=True)

    deleted = processor.execute(f"DELETE FROM tiendas WHERE DENTRO(ubicacion, {_DISTRITO})")
    remaining = processor.execute(f"SELECT id FROM tiendas WHERE DENTRO(ubicacion, {_DISTRITO})")
    all_rows = processor.execute("SELECT id FROM tiendas")

    assert deleted.affected_rows == len(_DENTRO)
    assert remaining.rows == ()
    assert {row[0] for row in all_rows.rows} == {3, 6}


def test_explain_de_dentro_no_lee_filas(tmp_path) -> None:
    _, processor = _processor(tmp_path, indexed=True)

    result = processor.execute(f"EXPLAIN SELECT * FROM tiendas WHERE DENTRO(ubicacion, {_DISTRITO})")

    assert result.rows == ()
    assert ("polygon_search", "rtree") in _operations(result)


def test_el_resultado_expone_el_poligono_para_el_mapa(tmp_path) -> None:
    _, processor = _processor(tmp_path, indexed=True)

    result = processor.execute(f"SELECT * FROM tiendas WHERE DENTRO(ubicacion, {_DISTRITO})")

    context = result.spatial_context
    assert context is not None
    assert context.kind == "polygon"
    assert context.table == "tiendas"
    assert context.column == "ubicacion"
    assert [(v.latitude, v.longitude) for v in context.vertices][:2] == [
        (-12.20, -77.20),
        (-12.20, -76.90),
    ]
