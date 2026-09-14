"""Pruebas puras de los operadores fisicos de SELECT."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pytest

from engine.executor.operators import execute_select
from engine.parser import SelectStatement, parse_sql
from engine.parser.ast import SqlTypeName
from engine.parser.bound_ast import BoundColumn, BoundSchema
from engine.parser.semantic import bind_select
from engine.planner.optimizer import (
    AccessRoute,
    IndexMetadata,
    PhysicalSelectPlan,
    TableMetadata,
    optimize_select,
)
from engine.planner.plan import Op, Structure

_INT32_MIN = -(2**31)
_INT32_MAX = 2**31 - 1
_EPOCH = date(1970, 1, 1)


@dataclass(frozen=True, order=True, slots=True)
class _Date:
    days: int = 0


class _Native:
    Date = _Date


@dataclass(frozen=True, slots=True)
class _RID:
    page: int
    slot: int


@dataclass(slots=True)
class _Stats:
    pages_read: int = 0
    pages_written: int = 0
    records_examined: int = 0
    records_returned: int = 0


class _FakeTable:
    def __init__(self, records: list[list[object]]) -> None:
        self.records = records
        self.rids = [_RID(1, slot) for slot in range(len(records))]
        self.by_rid = dict(zip(self.rids, records, strict=True))
        self.current_stats = _Stats(99, 98, 97, 96)
        self.reset_calls = 0
        self.search_calls: list[object] = []
        self.range_calls: list[tuple[object, object]] = []
        self.scan_calls = 0
        self.read_calls: list[_RID] = []

    def reset_stats(self) -> None:
        self.reset_calls += 1
        self.current_stats = _Stats()

    def stats(self) -> _Stats:
        return self.current_stats

    def scan(self) -> list[list[object]]:
        self.scan_calls += 1
        self.current_stats = _Stats(4, 0, len(self.records), len(self.records))
        return list(self.records)

    def search(self, key: object) -> list[list[object]]:
        self.search_calls.append(key)
        found = [row for row in self.records if _compare(row[0], key) == 0]
        self.current_stats = _Stats(2, 0, 1, len(found))
        return found

    def range_search(self, lo: object, hi: object) -> list[list[object]]:
        self.range_calls.append((lo, hi))
        found = [
            row for row in self.records if _compare(row[0], lo) >= 0 and _compare(row[0], hi) <= 0
        ]
        self.current_stats = _Stats(3, 0, len(found), len(found))
        return found

    def read(self, rid: _RID) -> list[object] | None:
        self.read_calls.append(rid)
        self.current_stats.pages_read += 1
        self.current_stats.records_examined += 1
        record = self.by_rid.get(rid)
        if record is not None:
            self.current_stats.records_returned += 1
        return record


class _FakeIndex:
    def __init__(
        self,
        table: _FakeTable,
        column: int,
        *,
        dangling: bool = False,
        range_supported: bool = True,
    ) -> None:
        self.entries = [
            (row[column], rid) for row, rid in zip(table.records, table.rids, strict=True)
        ]
        if dangling:
            self.entries.append((15.0, _RID(999, 1)))
        self.current_stats = _Stats(88, 87, 86, 85)
        self.reset_calls = 0
        self.search_calls: list[object] = []
        self.range_calls: list[tuple[object, object]] = []
        self.range_supported = range_supported

    def supports_range(self) -> bool:
        return self.range_supported

    def reset_stats(self) -> None:
        self.reset_calls += 1
        self.current_stats = _Stats()

    def stats(self) -> _Stats:
        return self.current_stats

    def search(self, key: object) -> list[_RID]:
        self.search_calls.append(key)
        found = [rid for current, rid in self.entries if _compare(current, key) == 0]
        self.current_stats = _Stats(2, 0, len(found), len(found))
        return found

    def range_search(self, lo: object, hi: object) -> list[_RID]:
        self.range_calls.append((lo, hi))
        found = [
            rid
            for current, rid in self.entries
            if _compare(current, lo) >= 0 and _compare(current, hi) <= 0
        ]
        self.current_stats = _Stats(3, 0, len(found), len(found))
        return found


class _FakeDatabase:
    def __init__(self, table: _FakeTable) -> None:
        self.table_handle = table
        self.indexes: dict[str, _FakeIndex] = {}
        self.index_open_calls: list[str] = []

    def table(self, name: str) -> _FakeTable:
        assert name == "datos"
        return self.table_handle

    def index(self, table: str, name: str) -> _FakeIndex:
        assert table == "datos"
        self.index_open_calls.append(name)
        return self.indexes[name]


@pytest.fixture()
def schema() -> BoundSchema:
    return BoundSchema(
        "datos",
        (
            BoundColumn("id", _sql_type("INT"), None),
            BoundColumn("promedio", _sql_type("DOUBLE"), None),
            BoundColumn("nombre", _sql_type("VARCHAR"), 12),
            BoundColumn("activo", _sql_type("BOOL"), None),
            BoundColumn("ingreso", _sql_type("DATE"), None),
        ),
        key_column=0,
    )


@pytest.fixture()
def table() -> _FakeTable:
    return _FakeTable(
        [
            [1, 10.0, "Ana", False, _Date((date(2024, 1, 1) - _EPOCH).days)],
            [2, 15.0, "Luis", True, _Date((date(2024, 2, 1) - _EPOCH).days)],
            [3, 15.0, "Zoe", False, _Date((date(2024, 3, 1) - _EPOCH).days)],
            [4, 20.0, "Ñu", True, _Date((date(2024, 4, 1) - _EPOCH).days)],
        ]
    )


def _sql_type(name: str) -> SqlTypeName:
    return SqlTypeName(name)


def _statement(source: str, schema: BoundSchema):
    parsed = parse_sql(source)
    assert isinstance(parsed, SelectStatement)
    return bind_select(parsed, schema, source)


def _physical(
    source: str,
    schema: BoundSchema,
    route: AccessRoute,
    *,
    residual: bool = False,
    index: IndexMetadata | None = None,
) -> PhysicalSelectPlan:
    statement = _statement(source, schema)
    return PhysicalSelectPlan(
        statement=statement,
        table=TableMetadata("datos", Structure.HEAP, (() if index is None else (index,))),
        route=route,
        index=index,
        residual_filter=residual,
    )


def _bplus(column: int, name: str = "ix") -> IndexMetadata:
    return IndexMetadata(name, column, Structure.BPLUS_UNCLUSTERED, supports_range=True)


def _hash(column: int, name: str = "ix_hash") -> IndexMetadata:
    return IndexMetadata(name, column, Structure.EXTENDIBLE_HASH, supports_range=False)


def _compare(left: object, right: object) -> int:
    if left < right:  # type: ignore[operator]
        return -1
    if right < left:  # type: ignore[operator]
        return 1
    return 0


def test_scan_wildcard_convierte_date_y_aisla_stats(
    schema: BoundSchema,
    table: _FakeTable,
) -> None:
    source = "SELECT * FROM datos"
    result = execute_select(
        _FakeDatabase(table),
        _Native,
        _physical(source, schema, AccessRoute.SCAN),
        source,
    )

    assert result.columns == ("id", "promedio", "nombre", "activo", "ingreso")
    assert result.rows[1] == (2, 15.0, "Luis", True, date(2024, 2, 1))
    assert result.root.op is Op.SCAN
    assert result.root.stats.pages_read == 4
    assert result.root.stats.records_returned == 4
    assert table.reset_calls == 1
    assert result.root.children == []


def test_scan_filter_y_proyeccion_forman_un_pipeline(
    schema: BoundSchema,
    table: _FakeTable,
) -> None:
    source = "SELECT nombre, id, nombre FROM datos WHERE promedio = 15"
    result = execute_select(
        _FakeDatabase(table),
        _Native,
        _physical(source, schema, AccessRoute.SCAN, residual=True),
        source,
    )

    assert result.columns == ("nombre", "id", "nombre")
    assert result.rows == (("Luis", 2, "Luis"), ("Zoe", 3, "Zoe"))
    project = result.root
    filter_step = project.children[0]
    scan = filter_step.children[0]
    assert [project.op, filter_step.op, scan.op] == [Op.PROJECT, Op.FILTER, Op.SCAN]
    assert project.stats.records_examined == project.stats.records_returned == 2
    assert filter_step.stats.records_examined == 4
    assert filter_step.stats.records_returned == 2
    assert filter_step.detail == "promedio = 15"
    assert project.detail == "nombre, id, nombre"


def test_busqueda_puntual_por_pk_no_agrega_filter(
    schema: BoundSchema,
    table: _FakeTable,
) -> None:
    source = "SELECT * FROM datos WHERE id = 3"
    result = execute_select(
        _FakeDatabase(table),
        _Native,
        _physical(source, schema, AccessRoute.TABLE_SEARCH),
        source,
    )

    assert [row[0] for row in result.rows] == [3]
    assert table.search_calls == [3]
    assert result.root.op is Op.SEARCH
    assert result.root.detail == "id = 3"


@pytest.mark.parametrize(
    ("operator", "residual", "expected", "bounds"),
    [
        ("<", True, [1, 2], (_INT32_MIN, 3)),
        ("<=", False, [1, 2, 3], (_INT32_MIN, 3)),
        (">", True, [4], (3, _INT32_MAX)),
        (">=", False, [3, 4], (3, _INT32_MAX)),
    ],
)
def test_rango_pk_respeta_strictness_sobre_api_inclusiva(
    schema: BoundSchema,
    table: _FakeTable,
    operator: str,
    residual: bool,
    expected: list[int],
    bounds: tuple[int, int],
) -> None:
    source = f"SELECT * FROM datos WHERE id {operator} 3"
    result = execute_select(
        _FakeDatabase(table),
        _Native,
        _physical(source, schema, AccessRoute.TABLE_RANGE, residual=residual),
        source,
    )

    assert [row[0] for row in result.rows] == expected
    assert table.range_calls == [bounds]
    if residual:
        assert result.root.op is Op.FILTER
        assert result.root.children[0].op is Op.RANGE_SEARCH
    else:
        assert result.root.op is Op.RANGE_SEARCH


def test_between_secundario_es_index_range_mas_fetch(
    schema: BoundSchema,
    table: _FakeTable,
) -> None:
    source = "SELECT * FROM datos WHERE promedio BETWEEN 15 AND 20"
    index_metadata = _bplus(1, "por_promedio")
    index = _FakeIndex(table, 1)
    database = _FakeDatabase(table)
    database.indexes[index_metadata.name] = index

    result = execute_select(
        database,
        _Native,
        _physical(source, schema, AccessRoute.INDEX_RANGE, index=index_metadata),
        source,
    )

    assert [row[0] for row in result.rows] == [2, 3, 4]
    assert index.range_calls == [(15.0, 20.0)]
    assert result.root.op is Op.FETCH
    assert result.root.structure is Structure.HEAP
    assert result.root.stats.pages_read == 3
    assert result.root.children[0].op is Op.INDEX_RANGE
    assert result.root.children[0].structure is Structure.BPLUS_UNCLUSTERED
    assert result.root.children[0].stats.pages_read == 3
    assert result.root.detail == "lee 3 registros por RID"
    assert table.reset_calls == 1
    assert index.reset_calls == 1


def test_rango_estricto_secundario_filtra_la_frontera(
    schema: BoundSchema,
    table: _FakeTable,
) -> None:
    source = "SELECT id FROM datos WHERE promedio < 15"
    index_metadata = _bplus(1)
    database = _FakeDatabase(table)
    database.indexes[index_metadata.name] = _FakeIndex(table, 1)

    result = execute_select(
        database,
        _Native,
        _physical(
            source,
            schema,
            AccessRoute.INDEX_RANGE,
            residual=True,
            index=index_metadata,
        ),
        source,
    )

    assert result.rows == ((1,),)
    assert [step.op for step in result.root.walk()] == [
        Op.INDEX_RANGE,
        Op.FETCH,
        Op.FILTER,
        Op.PROJECT,
    ]
    assert result.root.children[0].stats.records_examined == 3
    assert result.root.children[0].stats.records_returned == 1


def test_hash_no_aplicable_cae_a_scan_y_nunca_se_abre(
    schema: BoundSchema,
    table: _FakeTable,
) -> None:
    source = "SELECT * FROM datos WHERE promedio > 15"
    statement = _statement(source, schema)
    metadata = TableMetadata("datos", Structure.HEAP, (_hash(1),))
    physical = optimize_select(statement, metadata)
    database = _FakeDatabase(table)

    result = execute_select(database, _Native, physical, source)

    assert [row[0] for row in result.rows] == [4]
    assert physical.route is AccessRoute.SCAN
    assert database.index_open_calls == []
    assert result.root.op is Op.FILTER
    assert result.root.children[0].op is Op.SCAN


def test_date_usa_extremos_nativos_y_devuelve_date_python(
    table: _FakeTable,
) -> None:
    schema = BoundSchema(
        "datos",
        (
            BoundColumn("ingreso", _sql_type("DATE"), None),
            BoundColumn("id", _sql_type("INT"), None),
        ),
        key_column=0,
    )
    date_table = _FakeTable(
        [
            [_Date((date(2024, 1, 1) - _EPOCH).days), 1],
            [_Date((date(2024, 3, 1) - _EPOCH).days), 2],
        ]
    )
    source = "SELECT * FROM datos WHERE ingreso >= DATE '2024-02-01'"

    result = execute_select(
        _FakeDatabase(date_table),
        _Native,
        _physical(source, schema, AccessRoute.TABLE_RANGE),
        source,
    )

    ((lo, hi),) = date_table.range_calls
    assert lo == _Date((date(2024, 2, 1) - _EPOCH).days)
    assert hi == _Date(_INT32_MAX)
    assert result.rows == ((date(2024, 3, 1), 2),)


def test_between_invertido_devuelve_vacio_sin_error(
    schema: BoundSchema,
    table: _FakeTable,
) -> None:
    source = "SELECT * FROM datos WHERE id BETWEEN 4 AND 2"

    result = execute_select(
        _FakeDatabase(table),
        _Native,
        _physical(source, schema, AccessRoute.TABLE_RANGE),
        source,
    )

    assert table.range_calls == [(4, 2)]
    assert result.rows == ()
    assert result.root.op is Op.RANGE_SEARCH


def test_dos_ejecuciones_no_mezclan_stats(
    schema: BoundSchema,
    table: _FakeTable,
) -> None:
    source = "SELECT * FROM datos"
    database = _FakeDatabase(table)
    physical = _physical(source, schema, AccessRoute.SCAN)

    first = execute_select(database, _Native, physical, source)
    table.current_stats.pages_read = 500
    second = execute_select(database, _Native, physical, source)

    assert first.root.stats.pages_read == second.root.stats.pages_read == 4
    assert table.reset_calls == 2


def test_rid_inexistente_falla_en_vez_de_perder_una_fila(
    schema: BoundSchema,
    table: _FakeTable,
) -> None:
    source = "SELECT * FROM datos WHERE promedio = 15"
    index_metadata = _bplus(1)
    database = _FakeDatabase(table)
    database.indexes[index_metadata.name] = _FakeIndex(table, 1, dangling=True)

    with pytest.raises(RuntimeError, match="RID inexistente"):
        execute_select(
            database,
            _Native,
            _physical(source, schema, AccessRoute.INDEX_SEARCH, index=index_metadata),
            source,
        )


def test_executor_defiende_range_si_el_handle_real_no_lo_soporta(
    schema: BoundSchema,
    table: _FakeTable,
) -> None:
    source = "SELECT * FROM datos WHERE promedio > 15"
    index_metadata = _bplus(1)
    index = _FakeIndex(table, 1, range_supported=False)
    database = _FakeDatabase(table)
    database.indexes[index_metadata.name] = index

    with pytest.raises(RuntimeError, match="no lo soporta"):
        execute_select(
            database,
            _Native,
            _physical(
                source,
                schema,
                AccessRoute.INDEX_RANGE,
                residual=True,
                index=index_metadata,
            ),
            source,
        )

    assert index.range_calls == []
    assert index.reset_calls == 0


def test_plan_inconsistente_falla_antes_de_leer(
    schema: BoundSchema,
    table: _FakeTable,
) -> None:
    source = "SELECT * FROM datos WHERE id = 1"
    physical = _physical(source, schema, AccessRoute.SCAN)

    with pytest.raises(ValueError, match="scan con WHERE"):
        execute_select(_FakeDatabase(table), _Native, physical, source)

    assert table.scan_calls == 0
