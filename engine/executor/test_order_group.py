"""Pruebas E2E de ORDER BY y GROUP BY contra los algoritmos externos."""

from datetime import date

import pytest

from engine.executor import QueryProcessor

quipudb = pytest.importorskip(
    "quipudb_native",
    reason="los bindings no estan compilados: cmake -DQUIPUDB_BUILD_PYTHON=ON",
)

_EXTERNAL_BUFFERS = 3
_EXTERNAL_PAGE_SIZE = 128


def _processor(database, temp_dir, *, buffers: int = _EXTERNAL_BUFFERS):
    temp_dir.mkdir(exist_ok=True)
    return QueryProcessor(
        database,
        external_buffers=buffers,
        external_page_size=_EXTERNAL_PAGE_SIZE,
        temp_dir=temp_dir,
    )


def _step(result, operation: str):
    assert result.plan is not None
    matches = [step for step in result.plan.root.walk() if step.op.value == operation]
    assert len(matches) == 1
    return matches[0]


def _ops(result) -> list[str]:
    assert result.plan is not None
    return [step.op.value for step in result.plan.root.walk()]


def _assert_ops_in_order(result, *expected: str) -> None:
    operations = _ops(result)
    positions = [operations.index(operation) for operation in expected]
    assert positions == sorted(positions)


def _assert_temp_dir_is_clean(temp_dir) -> None:
    assert list(temp_dir.iterdir()) == []


def _create_order_data(database, temp_dir, *, rows: int = 25):
    processor = _processor(database, temp_dir)
    processor.execute(
        "CREATE TABLE ordenes (id INT PRIMARY KEY, prioridad INT, etiqueta VARCHAR(12)) USING HEAP"
    )
    inserted = []
    for row_id in range(rows):
        priority = (row_id * 7) % 5
        label = f"fila-{row_id:02d}"
        processor.execute(f"INSERT INTO ordenes VALUES ({row_id}, {priority}, '{label}')")
        inserted.append((row_id, priority))
    return processor, inserted


def _create_sales_data(database, temp_dir):
    processor = _processor(database, temp_dir)
    processor.execute(
        "CREATE TABLE ventas ("
        "id INT PRIMARY KEY, region VARCHAR(8), unidades INT, monto DOUBLE, "
        "etiqueta VARCHAR(8), activa BOOL, fecha DATE"
        ") USING HEAP"
    )
    values = (
        "(1, 'norte', 2, 10, 'zeta', FALSE, DATE '2024-01-03')",
        "(2, 'norte', 3, 20, 'alfa', TRUE, DATE '2024-01-01')",
        "(3, 'norte', 5, 15, 'beta', FALSE, DATE '2024-01-02')",
        "(4, 'sur', 4, 8, 'delta', TRUE, DATE '2024-02-02')",
        "(5, 'sur', 6, 12, 'gamma', TRUE, DATE '2024-02-01')",
        "(6, 'este', 7, 5, 'omega', FALSE, DATE '2024-03-01')",
    )
    for value in values:
        processor.execute(f"INSERT INTO ventas VALUES {value}")
    return processor


@pytest.mark.parametrize(
    ("suffix", "reverse", "direction"),
    [
        ("", False, "ASC"),
        (" ASC", False, "ASC"),
        (" DESC", True, "DESC"),
    ],
)
def test_order_by_asc_desc_es_estable_y_usa_external_sort(
    tmp_path,
    suffix: str,
    reverse: bool,
    direction: str,
) -> None:
    database = quipudb.Database(tmp_path / "catalogo.txt")
    temp_dir = tmp_path / "external"
    processor, inserted = _create_order_data(database, temp_dir)

    result = processor.execute(f"SELECT id, prioridad FROM ordenes ORDER BY prioridad{suffix}")

    assert result.columns == ("id", "prioridad")
    assert result.rows == tuple(sorted(inserted, key=lambda row: row[1], reverse=reverse))
    assert result.affected_rows == 0
    assert _ops(result) == ["scan", "sort", "project"]

    sort = _step(result, "sort")
    assert sort.structure.value == "external_sort"
    assert sort.column == "prioridad"
    assert direction.lower() in sort.detail.lower()
    assert "run" in sort.detail.lower()
    assert sort.stats.pages_read > 0
    assert sort.stats.pages_written > 0
    assert result.plan is not None
    assert result.plan.time_ms >= result.plan.root.subtree_time_ms()
    _assert_temp_dir_is_clean(temp_dir)


def test_order_by_pequeno_se_resuelve_en_memoria_sin_escrituras(tmp_path) -> None:
    database = quipudb.Database(tmp_path / "catalogo.txt")
    temp_dir = tmp_path / "external"
    processor, inserted = _create_order_data(database, temp_dir, rows=4)

    result = processor.execute("SELECT * FROM ordenes ORDER BY prioridad")

    expected = sorted(inserted, key=lambda row: row[1])
    assert [(row[0], row[1]) for row in result.rows] == expected
    sort = _step(result, "sort")
    assert sort.structure.value == "external_sort"
    assert sort.stats.pages_read == 0
    assert sort.stats.pages_written == 0
    assert any(word in sort.detail.lower() for word in ("memoria", "memory"))
    _assert_temp_dir_is_clean(temp_dir)


def test_group_by_calcula_todos_los_agregados_y_reordena_proyecciones(
    tmp_path,
) -> None:
    database = quipudb.Database(tmp_path / "catalogo.txt")
    temp_dir = tmp_path / "external"
    processor = _create_sales_data(database, temp_dir)

    result = processor.execute(
        "SELECT AVG(monto), region, COUNT(*), SUM(unidades), MIN(etiqueta), MAX(fecha) "
        "FROM ventas GROUP BY region"
    )

    assert result.columns == (
        "AVG_monto",
        "region",
        "COUNT_all",
        "SUM_unidades",
        "MIN_etiqueta",
        "MAX_fecha",
    )
    by_region = {row[1]: row for row in result.rows}
    assert by_region == {
        "norte": (15.0, "norte", 3, 10.0, "alfa", date(2024, 1, 3)),
        "sur": (10.0, "sur", 2, 10.0, "delta", date(2024, 2, 2)),
        "este": (5.0, "este", 1, 7.0, "omega", date(2024, 3, 1)),
    }
    _assert_ops_in_order(result, "scan", "group", "project")

    group = _step(result, "group")
    assert group.structure.value == "external_hash"
    assert group.column == "region"
    assert "hash" in group.detail.lower()
    assert "part" in group.detail.lower()
    assert "porque" in group.detail.lower()
    assert group.stats.records_examined == 6
    assert group.stats.records_returned == 3
    assert group.stats.pages_read > 0
    assert group.stats.pages_written > 0
    _assert_temp_dir_is_clean(temp_dir)


def test_group_by_se_puede_ordenar_por_su_clave_en_descendente(tmp_path) -> None:
    database = quipudb.Database(tmp_path / "catalogo.txt")
    temp_dir = tmp_path / "external"
    processor = _create_sales_data(database, temp_dir)

    result = processor.execute(
        "SELECT region, COUNT(*) FROM ventas GROUP BY region ORDER BY region DESC"
    )

    assert result.columns == ("region", "COUNT_all")
    assert result.rows == (("sur", 2), ("norte", 3), ("este", 1))
    _assert_ops_in_order(result, "scan", "group", "sort")
    sort = _step(result, "sort")
    assert sort.structure.value == "external_sort"
    assert sort.column == "region"
    assert "desc" in sort.detail.lower()
    _assert_temp_dir_is_clean(temp_dir)


def test_group_auto_explica_fallback_y_reutiliza_su_orden_ascendente(tmp_path) -> None:
    database = quipudb.Database(tmp_path / "catalogo.txt")
    temp_dir = tmp_path / "external"
    processor = _processor(database, temp_dir)
    processor.execute(
        "CREATE TABLE grande (id INT PRIMARY KEY, grupo VARCHAR(30), "
        "valor DOUBLE, unidades INT) USING HEAP"
    )
    table = database.table("grande")
    for row_id in range(1, 4001):
        table.insert([row_id, f"g{row_id}", float(row_id % 100), 1])

    result = processor.execute(
        "SELECT grupo, COUNT(*) FROM grande GROUP BY grupo ORDER BY grupo ASC"
    )

    assert len(result.rows) == 4000
    assert result.rows[0] == ("g1", 1)
    assert result.rows[-1] == ("g999", 1)
    assert [row[0] for row in result.rows] == sorted(row[0] for row in result.rows)
    assert _ops(result) == ["scan", "group", "project"]
    group = _step(result, "group")
    assert group.structure.value == "external_sort"
    assert "usada sort" in group.detail.lower()
    assert "fallback" in group.detail.lower()
    assert "no convergio" in group.detail.lower()
    assert "reutiliza" in group.detail.lower()
    assert group.stats.records_examined == 4000
    assert group.stats.records_returned == 4000
    assert group.stats.pages_read > 0
    assert group.stats.pages_written > 0
    _assert_temp_dir_is_clean(temp_dir)


def test_where_por_hash_alimenta_order_by_antes_de_proyectar(tmp_path) -> None:
    database = quipudb.Database(tmp_path / "catalogo.txt")
    temp_dir = tmp_path / "external"
    processor = _create_sales_data(database, temp_dir)
    database.create_index(
        "ventas",
        "por_region",
        "region",
        quipudb.kind.EXTENDIBLE_HASH,
    )

    result = processor.execute(
        "SELECT id, monto FROM ventas WHERE region = 'norte' ORDER BY monto DESC"
    )

    assert result.rows == ((2, 20.0), (3, 15.0), (1, 10.0))
    assert _ops(result) == ["index_search", "fetch", "sort", "project"]
    assert _step(result, "index_search").structure.value == "extendible_hash"
    assert _step(result, "sort").structure.value == "external_sort"
    _assert_temp_dir_is_clean(temp_dir)


def test_filtro_residual_se_encadena_con_order_by_sin_lista_intermedia(tmp_path) -> None:
    database = quipudb.Database(tmp_path / "catalogo.txt")
    temp_dir = tmp_path / "external"
    processor, inserted = _create_order_data(database, temp_dir)

    result = processor.execute(
        "SELECT id, prioridad FROM ordenes WHERE prioridad >= 2 "
        "ORDER BY prioridad DESC"
    )

    expected = sorted(
        (row for row in inserted if row[1] >= 2),
        key=lambda row: row[1],
        reverse=True,
    )
    assert result.rows == tuple(expected)
    assert _ops(result) == ["scan", "filter", "sort", "project"]
    assert _step(result, "filter").stats.records_examined == len(inserted)
    assert _step(result, "filter").stats.records_returned == len(expected)
    _assert_temp_dir_is_clean(temp_dir)


def test_where_por_bplus_alimenta_group_by(tmp_path) -> None:
    database = quipudb.Database(tmp_path / "catalogo.txt")
    temp_dir = tmp_path / "external"
    processor = _create_sales_data(database, temp_dir)
    database.create_index(
        "ventas",
        "por_monto",
        "monto",
        quipudb.kind.BPLUS_UNCLUSTERED,
    )

    result = processor.execute(
        "SELECT region, COUNT(*), SUM(unidades) FROM ventas "
        "WHERE monto BETWEEN 8 AND 20 GROUP BY region"
    )

    assert result.columns == ("region", "COUNT_all", "SUM_unidades")
    assert {row[0]: row[1:] for row in result.rows} == {
        "norte": (3, 10.0),
        "sur": (2, 10.0),
    }
    _assert_ops_in_order(result, "index_range", "fetch", "group")
    assert _step(result, "index_range").structure.value == "bplus_unclustered"
    assert _step(result, "group").structure.value == "external_hash"
    _assert_temp_dir_is_clean(temp_dir)


def test_order_y_group_siguen_funcionando_despues_de_reabrir(tmp_path) -> None:
    catalog = tmp_path / "catalogo.txt"
    temp_dir = tmp_path / "external"
    database = quipudb.Database(catalog)
    processor = _create_sales_data(database, temp_dir)
    database.flush()
    database.close("ventas")
    del processor, database

    reopened = quipudb.Database(catalog)
    processor = _processor(reopened, temp_dir)
    ordered = processor.execute("SELECT id, monto FROM ventas ORDER BY monto DESC")
    grouped = processor.execute("SELECT region, COUNT(*) FROM ventas GROUP BY region")

    assert [row[0] for row in ordered.rows] == [2, 3, 5, 1, 4, 6]
    assert {row[0]: row[1] for row in grouped.rows} == {
        "norte": 3,
        "sur": 2,
        "este": 1,
    }
    assert _step(ordered, "sort").structure.value == "external_sort"
    assert _step(grouped, "group").structure.value == "external_hash"
    _assert_temp_dir_is_clean(temp_dir)
