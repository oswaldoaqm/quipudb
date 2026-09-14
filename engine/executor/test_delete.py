"""Pruebas puras de captura y eliminacion consistente de filas."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from engine.executor.dml import (
    DeleteCandidate,
    collect_delete_candidates,
    delete_with_indexes,
    execute_delete,
)
from engine.parser import DeleteStatement, parse_sql
from engine.parser.ast import SqlTypeName
from engine.parser.bound_ast import BoundColumn, BoundSchema
from engine.parser.semantic import bind_delete
from engine.planner.optimizer import (
    AccessRoute,
    IndexMetadata,
    TableMetadata,
    optimize_delete,
)
from engine.planner.plan import Structure


class _InjectedError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class _RID:
    page: int
    slot: int


class _Native:
    pass


class _Table:
    def __init__(self, rows: list[list[object]]) -> None:
        self.rows = [list(row) for row in rows]
        self.rids = [_RID(1, slot) for slot in range(len(rows))]
        self.next_slot = len(rows)
        self.scan_calls = 0
        self.search_calls: list[object] = []
        self.range_calls: list[tuple[object, object]] = []
        self.remove_calls: list[object] = []
        self.insert_calls: list[list[object]] = []
        self.fail_remove: dict[object, Exception] = {}
        self.fail_insert: Exception | None = None

    def scan_with_rids(self) -> list[tuple[_RID, list[object]]]:
        self.scan_calls += 1
        return [
            (rid, list(row))
            for rid, row in zip(self.rids, self.rows, strict=True)
        ]

    def search(self, key: object) -> list[list[object]]:
        self.search_calls.append(key)
        return [list(row) for row in self.rows if row[0] == key]

    def range_search(self, lower: object, upper: object) -> list[list[object]]:
        self.range_calls.append((lower, upper))
        return [list(row) for row in self.rows if lower <= row[0] <= upper]  # type: ignore[operator]

    def read(self, rid: _RID) -> list[object] | None:
        try:
            position = self.rids.index(rid)
        except ValueError:
            return None
        return list(self.rows[position])

    def remove(self, key: object) -> int:
        self.remove_calls.append(key)
        if error := self.fail_remove.get(key):
            raise error
        for position, row in enumerate(self.rows):
            if row[0] == key:
                self.rows.pop(position)
                self.rids.pop(position)
                return 1
        return 0

    def insert(self, record: list[object]) -> _RID:
        self.insert_calls.append(list(record))
        if self.fail_insert is not None:
            raise self.fail_insert
        if any(row[0] == record[0] for row in self.rows):
            raise RuntimeError("clave primaria duplicada")
        rid = _RID(2, self.next_slot)
        self.next_slot += 1
        self.rows.append(list(record))
        self.rids.append(rid)
        return rid


class _Index:
    def __init__(
        self,
        entries: list[tuple[object, _RID]],
        *,
        supports_range: bool = False,
    ) -> None:
        self.entries = list(entries)
        self.range_supported = supports_range
        self.remove_calls: list[tuple[object, _RID | None]] = []
        self.insert_calls: list[tuple[object, _RID]] = []
        self.fail_remove: Exception | None = None
        self.fail_insert: Exception | None = None
        self.duplicate_search_result = False
        self.dangling_search_result: _RID | None = None

    def search(self, key: object) -> list[_RID]:
        matches = [rid for current, rid in self.entries if current == key]
        if self.duplicate_search_result and matches:
            matches.append(matches[0])
        if self.dangling_search_result is not None:
            matches.append(self.dangling_search_result)
        return matches

    def range_search(self, lower: object, upper: object) -> list[_RID]:
        return [
            rid
            for key, rid in self.entries
            if lower <= key <= upper  # type: ignore[operator]
        ]

    def supports_range(self) -> bool:
        return self.range_supported

    def remove_one(self, key: object, rid: _RID | None) -> bool:
        self.remove_calls.append((key, rid))
        if self.fail_remove is not None:
            raise self.fail_remove
        try:
            self.entries.remove((key, rid))  # type: ignore[arg-type]
        except ValueError:
            return False
        return True

    def insert(self, key: object, rid: _RID) -> None:
        self.insert_calls.append((key, rid))
        if self.fail_insert is not None:
            raise self.fail_insert
        self.entries.append((key, rid))


class _Database:
    def __init__(self, table: _Table) -> None:
        self.table_handle = table
        self.indexes: dict[str, _Index] = {}

    def table(self, name: str) -> _Table:
        assert name == "datos"
        return self.table_handle

    def index(self, table: str, name: str) -> _Index:
        assert table == "datos"
        return self.indexes[name]


@pytest.fixture()
def schema() -> BoundSchema:
    return BoundSchema(
        "datos",
        (
            BoundColumn("id", SqlTypeName.INT, None),
            BoundColumn("grupo", SqlTypeName.INT, None),
            BoundColumn("nombre", SqlTypeName.VARCHAR, 20),
        ),
        key_column=0,
    )


@pytest.fixture()
def table() -> _Table:
    return _Table(
        [
            [1, 7, "Ada"],
            [2, 7, "Grace"],
            [3, 8, "Ada"],
            [4, 9, "Edsger"],
        ]
    )


def _metadata(
    name: str,
    column: int,
    structure: Structure,
) -> IndexMetadata:
    return IndexMetadata(
        name,
        column,
        structure,
        supports_range=structure is Structure.BPLUS_UNCLUSTERED,
    )


def _plan(
    source: str,
    schema: BoundSchema,
    *indexes: IndexMetadata,
):
    parsed = parse_sql(source)
    assert isinstance(parsed, DeleteStatement)
    bound = bind_delete(parsed, schema, source)
    return optimize_delete(bound, TableMetadata("datos", Structure.HEAP, indexes))


def _candidate(table: _Table, key: int) -> DeleteCandidate:
    position = next(i for i, row in enumerate(table.rows) if row[0] == key)
    return DeleteCandidate(table.rids[position], tuple(table.rows[position]))


def test_captura_por_hash_devuelve_rid_y_registro(
    schema: BoundSchema,
    table: _Table,
) -> None:
    metadata = _metadata("por_grupo", 1, Structure.EXTENDIBLE_HASH)
    database = _Database(table)
    database.indexes[metadata.name] = _Index([(row[1], rid) for row, rid in zip(table.rows, table.rids, strict=True)])
    plan = _plan("DELETE FROM datos WHERE grupo = 7", schema, metadata)

    candidates = collect_delete_candidates(database, _Native, plan)

    assert plan.route is AccessRoute.INDEX_SEARCH
    assert [(candidate.rid, candidate.record[0]) for candidate in candidates] == [
        (_RID(1, 0), 1),
        (_RID(1, 1), 2),
    ]
    assert table.scan_calls == 0


def test_rango_estricto_por_bplus_filtra_la_frontera(
    schema: BoundSchema,
    table: _Table,
) -> None:
    metadata = _metadata("por_grupo", 1, Structure.BPLUS_UNCLUSTERED)
    database = _Database(table)
    database.indexes[metadata.name] = _Index(
        [(row[1], rid) for row, rid in zip(table.rows, table.rids, strict=True)],
        supports_range=True,
    )
    plan = _plan("DELETE FROM datos WHERE grupo < 8", schema, metadata)

    candidates = collect_delete_candidates(database, _Native, plan)

    assert plan.route is AccessRoute.INDEX_RANGE
    assert plan.residual_filter is True
    assert [candidate.record[0] for candidate in candidates] == [1, 2]


def test_scan_con_rid_resuelve_fallback_y_between_invertido(
    schema: BoundSchema,
    table: _Table,
) -> None:
    metadata = _metadata("por_nombre", 2, Structure.EXTENDIBLE_HASH)
    database = _Database(table)
    plan = _plan("DELETE FROM datos WHERE grupo BETWEEN 8 AND 7", schema, metadata)

    candidates = collect_delete_candidates(database, _Native, plan)

    assert plan.route is AccessRoute.SCAN
    assert candidates == ()
    assert table.scan_calls == 1


def test_indice_con_rid_duplicado_o_colgante_falla_antes_de_mutar(
    schema: BoundSchema,
    table: _Table,
) -> None:
    metadata = _metadata("por_grupo", 1, Structure.EXTENDIBLE_HASH)
    database = _Database(table)
    index = _Index([(7, table.rids[0])])
    database.indexes[metadata.name] = index
    plan = _plan("DELETE FROM datos WHERE grupo = 7", schema, metadata)

    index.duplicate_search_result = True
    with pytest.raises(RuntimeError, match="RID duplicado"):
        collect_delete_candidates(database, _Native, plan)

    index.duplicate_search_result = False
    index.dangling_search_result = _RID(99, 0)
    with pytest.raises(RuntimeError, match="RID inexistente"):
        collect_delete_candidates(database, _Native, plan)
    assert table.remove_calls == []


def test_delete_actualiza_dos_indices_sin_borrar_claves_repetidas_vecinas(
    schema: BoundSchema,
    table: _Table,
) -> None:
    by_group = _metadata("por_grupo", 1, Structure.EXTENDIBLE_HASH)
    by_name = _metadata("por_nombre", 2, Structure.BPLUS_UNCLUSTERED)
    database = _Database(table)
    database.indexes[by_group.name] = _Index(
        [(row[1], rid) for row, rid in zip(table.rows, table.rids, strict=True)]
    )
    database.indexes[by_name.name] = _Index(
        [(row[2], rid) for row, rid in zip(table.rows, table.rids, strict=True)],
        supports_range=True,
    )
    plan = _plan("DELETE FROM datos WHERE grupo = 7", schema, by_group, by_name)

    affected = execute_delete(database, _Native, plan)

    assert affected == 2
    assert [row[0] for row in table.rows] == [3, 4]
    assert database.indexes[by_group.name].entries == [(8, _RID(1, 2)), (9, _RID(1, 3))]
    assert database.indexes[by_name.name].entries == [
        ("Ada", _RID(1, 2)),
        ("Edsger", _RID(1, 3)),
    ]


def test_cero_candidatos_no_modifica_ninguna_estructura(table: _Table) -> None:
    metadata = _metadata("por_grupo", 1, Structure.EXTENDIBLE_HASH)
    database = _Database(table)
    index = _Index([(row[1], rid) for row, rid in zip(table.rows, table.rids, strict=True)])
    database.indexes[metadata.name] = index
    before_rows = [list(row) for row in table.rows]
    before_entries = list(index.entries)

    affected = delete_with_indexes(database, "datos", (), 0, [metadata])

    assert affected == 0
    assert table.rows == before_rows
    assert index.entries == before_entries
    assert table.remove_calls == []
    assert index.remove_calls == []


def test_fallo_del_segundo_indice_restaura_el_primero_y_no_toca_tabla(
    table: _Table,
) -> None:
    first = _metadata("primero", 1, Structure.EXTENDIBLE_HASH)
    second = _metadata("segundo", 2, Structure.EXTENDIBLE_HASH)
    database = _Database(table)
    database.indexes[first.name] = _Index([(7, table.rids[0])])
    original = _InjectedError("fallo del segundo indice")
    database.indexes[second.name] = _Index([("Ada", table.rids[0])])
    database.indexes[second.name].fail_remove = original

    with pytest.raises(_InjectedError) as caught:
        delete_with_indexes(database, "datos", (_candidate(table, 1),), 0, [first, second])

    assert caught.value is original
    assert database.indexes[first.name].entries == [(7, table.rids[0])]
    assert table.remove_calls == []
    assert [row[0] for row in table.rows] == [1, 2, 3, 4]


def test_fallo_de_tabla_reinserta_filas_con_rid_nuevo_y_repara_indices(
    table: _Table,
) -> None:
    metadata = _metadata("por_grupo", 1, Structure.EXTENDIBLE_HASH)
    database = _Database(table)
    index = _Index([(row[1], rid) for row, rid in zip(table.rows, table.rids, strict=True)])
    database.indexes[metadata.name] = index
    original = _InjectedError("fallo al borrar la segunda fila")
    table.fail_remove[2] = original
    candidates = (_candidate(table, 1), _candidate(table, 2))

    with pytest.raises(_InjectedError) as caught:
        delete_with_indexes(database, "datos", candidates, 0, [metadata])

    assert caught.value is original
    assert {row[0] for row in table.rows} == {1, 2, 3, 4}
    rid_by_key = {row[0]: rid for rid, row in zip(table.rids, table.rows, strict=True)}
    assert rid_by_key[1] != _RID(1, 0)
    assert (7, rid_by_key[1]) in index.entries
    assert (7, rid_by_key[2]) in index.entries
    assert all(table.read(rid) is not None for _key, rid in index.entries)


def test_remove_one_ausente_no_borra_la_fila_y_restaura_lo_anterior(
    table: _Table,
) -> None:
    first = _metadata("primero", 1, Structure.EXTENDIBLE_HASH)
    missing = _metadata("faltante", 2, Structure.EXTENDIBLE_HASH)
    database = _Database(table)
    database.indexes[first.name] = _Index([(7, table.rids[0])])
    database.indexes[missing.name] = _Index([])

    with pytest.raises(RuntimeError, match="no contiene"):
        delete_with_indexes(database, "datos", (_candidate(table, 1),), 0, [first, missing])

    assert database.indexes[first.name].entries == [(7, table.rids[0])]
    assert table.read(table.rids[0]) == [1, 7, "Ada"]


def test_rollback_fallido_no_oculta_la_excepcion_original(table: _Table) -> None:
    first = _metadata("primero", 1, Structure.EXTENDIBLE_HASH)
    second = _metadata("segundo", 2, Structure.EXTENDIBLE_HASH)
    database = _Database(table)
    first_index = _Index([(7, table.rids[0])])
    first_index.fail_insert = _InjectedError("fallo secundario de rollback")
    database.indexes[first.name] = first_index
    original = _InjectedError("fallo original")
    second_index = _Index([("Ada", table.rids[0])])
    second_index.fail_remove = original
    database.indexes[second.name] = second_index

    with pytest.raises(_InjectedError) as caught:
        delete_with_indexes(database, "datos", (_candidate(table, 1),), 0, [first, second])

    assert caught.value is original
