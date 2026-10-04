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


@pytest.mark.parametrize("limit", [0, 1, 2, 10, 2**100])
def test_knn_devuelve_vecinos_en_orden_y_absorbe_limit(tmp_path, limit) -> None:
    _, processor = _processor(tmp_path)
    processor.execute("CREATE INDEX tiendas_ubicacion ON tiendas (ubicacion) USING RTREE")
    result = processor.execute(
        f"SELECT id FROM tiendas ORDER BY distancia(ubicacion, {_CENTER}) LIMIT {limit}"
    )
    assert result.rows == ((1,), (2,), (3,))[:limit]
    assert _operations(result) == [
        ("knn_search", "rtree"), ("fetch", "heap"), ("project", "memory"),
    ]
    knn = result.plan.root.walk()[0]
    assert knn.stats.records_returned == min(limit, 3)
    assert f"k={limit}" in knn.detail
    assert "HAVERSINE" in knn.detail
    if limit == 0:
        assert result.plan.root.subtree_stats().pages_read == 0


@pytest.mark.parametrize("indexed", [False, True])
@pytest.mark.parametrize("suffix", ["", " DESC", " DESC LIMIT 2"])
def test_orden_espacial_completo_y_descendente_conserva_las_filas(tmp_path, indexed, suffix) -> None:
    _, processor = _processor(tmp_path)
    if indexed:
        processor.execute("CREATE INDEX tiendas_ubicacion ON tiendas (ubicacion) USING RTREE")
    result = processor.execute(
        f"SELECT * FROM tiendas ORDER BY distancia(ubicacion, {_CENTER}){suffix}"
    )
    expected = (3, 2, 1) if "DESC" in suffix else (1, 2, 3)
    if "LIMIT" in suffix:
        expected = expected[:2]
    assert tuple(row[0] for row in result.rows) == expected
    assert len(result.columns) == 3
    assert all(len(row) == 3 for row in result.rows)
    assert ("sort", "external_sort") in _operations(result)
    assert ("knn_search", "rtree") not in _operations(result)


def test_order_by_distancia_sin_indice_ordena_antes_de_limit(tmp_path) -> None:
    _, processor = _processor(tmp_path)
    result = processor.execute(
        f"SELECT nombre FROM tiendas ORDER BY distancia(ubicacion, {_CENTER}) LIMIT 2"
    )
    assert result.rows == (("centro",), ("cerca",))
    assert _operations(result) == [
        ("scan", "heap"), ("sort", "external_sort"),
        ("project", "memory"), ("limit", "memory"),
    ]


def test_order_by_espacial_filtra_antes_de_elegir_los_k(tmp_path) -> None:
    _, processor = _processor(tmp_path)
    processor.execute("CREATE INDEX tiendas_ubicacion ON tiendas (ubicacion) USING RTREE")
    result = processor.execute(
        f"SELECT id FROM tiendas WHERE id > 1 ORDER BY distancia(ubicacion, {_CENTER}) LIMIT 1"
    )
    assert result.rows == ((2,),)
    assert ("sort", "external_sort") in _operations(result)
    assert ("knn_search", "rtree") not in _operations(result)


@pytest.mark.parametrize("indexed", [False, True])
def test_order_by_distancia_respeta_la_metrica_que_cambia_el_vecino(tmp_path, indexed) -> None:
    _, processor = _processor(tmp_path)
    processor.execute("DROP TABLE tiendas")
    processor.execute("CREATE TABLE tiendas (id INT PRIMARY KEY, ubicacion POINT)")
    processor.execute("INSERT INTO tiendas VALUES (1, POINT(80, 10))")
    processor.execute("INSERT INTO tiendas VALUES (2, POINT(82, 0))")
    if indexed:
        processor.execute("CREATE INDEX tiendas_ubicacion ON tiendas (ubicacion) USING RTREE")
    haversine = processor.execute(
        "SELECT id FROM tiendas ORDER BY distancia(ubicacion, POINT(80, 0)) LIMIT 1"
    )
    euclidean = processor.execute(
        "SELECT id FROM tiendas ORDER BY distancia(ubicacion, POINT(80, 0), EUCLIDEAN) LIMIT 1"
    )
    assert haversine.rows == ((1,),)
    assert euclidean.rows == ((2,),)


def test_explain_knn_y_analyze_declaran_la_misma_ruta(tmp_path) -> None:
    _, processor = _processor(tmp_path)
    processor.execute("CREATE INDEX tiendas_ubicacion ON tiendas (ubicacion) USING RTREE")
    query = f"SELECT * FROM tiendas ORDER BY distancia(ubicacion, {_CENTER}) LIMIT 2"
    planned = processor.execute("EXPLAIN " + query)
    analyzed = processor.execute("EXPLAIN ANALYZE " + query)
    assert planned.rows == analyzed.rows == ()
    assert _operations(planned) == _operations(analyzed) == [
        ("knn_search", "rtree"), ("fetch", "heap"),
    ]
    assert all(step.stats.records_examined == 0 for step in planned.plan.root.walk())
    assert analyzed.plan.root.walk()[0].stats.records_returned == 2


def test_knn_poda_en_vez_de_ordenar_toda_la_tabla(tmp_path) -> None:
    database = quipudb.Database(tmp_path / "catalogo.txt")
    processor = QueryProcessor(database, external_buffers=3, external_page_size=128)
    processor.execute("CREATE TABLE puntos (id INT PRIMARY KEY, ubicacion POINT)")
    table = database.table("puntos")
    for index in range(1200):
        table.insert([index, quipudb.GeoPoint(0, index / 1000)])
    query = "SELECT id FROM puntos ORDER BY distancia(ubicacion, POINT(0, 0)) LIMIT 10"
    scanned = processor.execute(query)
    processor.execute("CREATE INDEX puntos_ubicacion ON puntos (ubicacion) USING RTREE")
    nearest = processor.execute(query)
    assert nearest.rows == scanned.rows == tuple((index,) for index in range(10))
    assert _operations(nearest)[0] == ("knn_search", "rtree")
    assert nearest.plan.root.walk()[0].stats.records_examined < 1200
    assert scanned.plan.root.walk()[0].stats.records_examined >= 1200
    assert scanned.plan.root.walk()[1].stats.pages_written > 0


def test_distancia_agrupada_ordena_los_grupos_y_no_limita_filas_de_entrada(tmp_path) -> None:
    _, processor = _processor(tmp_path)
    processor.execute("CREATE INDEX tiendas_ubicacion ON tiendas (ubicacion) USING RTREE")
    result = processor.execute(
        "SELECT ubicacion, COUNT(*) FROM tiendas GROUP BY ubicacion "
        f"ORDER BY distancia(ubicacion, {_CENTER}) LIMIT 2"
    )
    assert tuple(row[1] for row in result.rows) == (1, 1)
    assert tuple(row[0].latitude for row in result.rows) == (-12.0464, -12.05)
    assert ("knn_search", "rtree") not in _operations(result)
    assert ("sort", "external_sort") in _operations(result)


def test_knn_en_tabla_vacia_y_empates_no_inventa_filas(tmp_path) -> None:
    _, processor = _processor(tmp_path)
    processor.execute("DROP TABLE tiendas")
    processor.execute("CREATE TABLE tiendas (id INT PRIMARY KEY, ubicacion POINT)")
    processor.execute("CREATE INDEX tiendas_ubicacion ON tiendas (ubicacion) USING RTREE")
    query = f"SELECT id FROM tiendas ORDER BY distancia(ubicacion, {_CENTER}) LIMIT 2"
    assert processor.execute(query).rows == ()
    for index in range(4):
        processor.execute(f"INSERT INTO tiendas VALUES ({index}, {_CENTER})")
    result = processor.execute(query)
    assert len(result.rows) == 2
    assert len(set(result.rows)) == 2


def test_distancia_sobre_join_ordena_la_salida_sin_aplicar_knn_a_una_hoja(tmp_path) -> None:
    _, processor = _processor(tmp_path)
    processor.execute("CREATE INDEX tiendas_ubicacion ON tiendas (ubicacion) USING RTREE")
    processor.execute("CREATE TABLE visitas (id INT PRIMARY KEY, tienda INT)")
    processor.execute("INSERT INTO visitas VALUES (1, 2); INSERT INTO visitas VALUES (2, 2)")
    result = processor.execute(
        "SELECT visitas.id FROM visitas JOIN tiendas ON visitas.tienda = tiendas.id "
        f"ORDER BY distancia(tiendas.ubicacion, {_CENTER}) LIMIT 2"
    )
    assert set(result.rows) == {(1,), (2,)}
    assert ("sort", "external_sort") in _operations(result)
    assert ("knn_search", "rtree") not in _operations(result)
