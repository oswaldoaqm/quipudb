"""Pruebas puras de ``QueryProcessor`` con dobles del modulo nativo."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from typing import ClassVar

import pytest

from engine.executor.processor import QueryProcessor
from engine.parser.errors import SQLSemanticError, SQLUnsupportedError


class _NativeError(Exception):
    """Base equivalente a ``quipudb_native.QuipuDBError``."""


class _SchemaError(_NativeError):
    pass


class _InvalidRecord(_NativeError):
    pass


class _DuplicateKey(_NativeError):
    pass


class _IoError(_NativeError):
    pass


class _DataType(StrEnum):
    INT = "INT"
    DOUBLE = "DOUBLE"
    VARCHAR = "VARCHAR"
    BOOL = "BOOL"
    DATE = "DATE"


class _Kind:
    HEAP: ClassVar[str] = "heap"
    SEQUENTIAL: ClassVar[str] = "sequential"
    BPLUS_UNCLUSTERED: ClassVar[str] = "bplus_unclustered"
    EXTENDIBLE_HASH: ClassVar[str] = "extendible_hash"


@dataclass
class _Date:
    days: int = 0


@dataclass
class _RID:
    page: int = 0
    slot: int = 0


@dataclass
class _Column:
    name: str
    type: _DataType
    length: int = 0


@dataclass
class _Schema:
    table_name: str
    columns: list[_Column]
    key_column: int = 0


class _NativeModule:
    QuipuDBError = _NativeError
    SchemaError = _SchemaError
    InvalidRecord = _InvalidRecord
    DuplicateKey = _DuplicateKey
    IoError = _IoError
    DataType = _DataType
    Date = _Date
    RID = _RID
    Column = _Column
    Schema = _Schema
    kind = _Kind


@dataclass
class _IndexInfo:
    name: str
    column: int
    kind: str = _Kind.EXTENDIBLE_HASH
    file: str = "fake-index.qdb"


@dataclass
class _TableInfo:
    schema: _Schema
    storage: str
    indexes: list[_IndexInfo] = field(default_factory=list)
    file: str = "fake-table.qdb"
    page_size: int = 4096


@dataclass
class _Stats:
    pages_read: int = 0
    pages_written: int = 0
    records_examined: int = 0
    records_returned: int = 0


class _FakeIndex:
    def __init__(self) -> None:
        self.entries: list[tuple[object, _RID]] = []
        self.insert_calls: list[tuple[object, _RID]] = []
        self.remove_calls: list[tuple[object, _RID]] = []
        self.fail_insert: _NativeError | None = None
        self.fail_remove: _NativeError | None = None

    def insert(self, key: object, rid: _RID) -> None:
        self.insert_calls.append((key, rid))
        if self.fail_insert is not None:
            raise self.fail_insert
        self.entries.append((key, rid))

    def remove_one(self, key: object, rid: _RID) -> bool:
        self.remove_calls.append((key, rid))
        if self.fail_remove is not None:
            raise self.fail_remove
        try:
            self.entries.remove((key, rid))
        except ValueError:
            return False
        return True

    def stats(self) -> _Stats:
        return _Stats()

    def reset_stats(self) -> None:
        pass


class _FakeTable:
    def __init__(self, schema: _Schema, storage: str) -> None:
        self.schema = schema
        self.kind = storage
        self.records: list[list[object]] = []
        self.insert_calls: list[list[object]] = []
        self.remove_calls: list[object] = []
        self.fail_insert: _NativeError | None = None
        self.fail_remove: _NativeError | None = None
        self._next_slot = 0

    def insert(self, record: list[object]) -> _RID:
        self.insert_calls.append(record)
        if self.fail_insert is not None:
            raise self.fail_insert
        key_column = self.schema.key_column
        if any(current[key_column] == record[key_column] for current in self.records):
            raise _DuplicateKey("la clave primaria ya existe")
        rid = _RID(1, self._next_slot)
        self._next_slot += 1
        self.records.append(record)
        return rid

    def remove(self, key: object) -> int:
        self.remove_calls.append(key)
        if self.fail_remove is not None:
            raise self.fail_remove
        key_column = self.schema.key_column
        for position, record in enumerate(self.records):
            if record[key_column] == key:
                self.records.pop(position)
                return 1
        return 0

    def stats(self) -> _Stats:
        return _Stats()

    def reset_stats(self) -> None:
        pass


class _FakeDatabase:
    def __init__(self) -> None:
        self.tables: dict[str, _FakeTable] = {}
        self.infos: dict[str, _TableInfo] = {}
        self.indexes: dict[tuple[str, str], _FakeIndex] = {}
        self.create_calls: list[tuple[_Schema, str]] = []
        self.fail_create: _NativeError | None = None
        self.fail_index_open: _NativeError | None = None

    def create_table(self, schema: _Schema, storage: str) -> _FakeTable:
        self.create_calls.append((schema, storage))
        if self.fail_create is not None:
            raise self.fail_create
        if schema.table_name in self.tables:
            raise _SchemaError(f"la tabla {schema.table_name} ya existe")
        table = _FakeTable(schema, storage)
        self.tables[schema.table_name] = table
        self.infos[schema.table_name] = _TableInfo(schema, storage)
        return table

    def has_table(self, name: str) -> bool:
        return name in self.tables

    def table(self, name: str) -> _FakeTable:
        try:
            return self.tables[name]
        except KeyError as exc:
            raise _SchemaError(f"tabla inexistente: {name}") from exc

    def table_info(self, name: str) -> _TableInfo:
        try:
            return self.infos[name]
        except KeyError as exc:
            raise _SchemaError(f"tabla inexistente: {name}") from exc

    def index(self, table: str, name: str) -> _FakeIndex:
        if self.fail_index_open is not None:
            raise self.fail_index_open
        return self.indexes[(table, name)]

    def add_index(self, table: str, name: str, column: int) -> _FakeIndex:
        index = _FakeIndex()
        self.indexes[(table, name)] = index
        self.infos[table].indexes.append(_IndexInfo(name, column))
        return index


@pytest.fixture()
def database() -> _FakeDatabase:
    return _FakeDatabase()


@pytest.fixture()
def processor(database: _FakeDatabase) -> QueryProcessor:
    return QueryProcessor(database, native_module=_NativeModule)


@pytest.mark.parametrize(
    ("storage_sql", "expected_storage"),
    [
        ("HEAP", _Kind.HEAP),
        ("SEQUENTIAL", _Kind.SEQUENTIAL),
    ],
)
def test_create_table_construye_el_esquema_y_elige_storage(
    database: _FakeDatabase,
    processor: QueryProcessor,
    storage_sql: str,
    expected_storage: str,
) -> None:
    result = processor.execute(
        "CREATE TABLE alumnos ("
        "codigo INT PRIMARY KEY, nombre VARCHAR(20), promedio DOUBLE, "
        f"activo BOOL, ingreso DATE) USING {storage_sql}"
    )

    assert result.columns == ()
    assert result.rows == ()
    assert result.affected_rows == 0
    ((schema, storage),) = database.create_calls
    assert storage == expected_storage
    assert schema.table_name == "alumnos"
    assert schema.key_column == 0
    assert [(column.name, column.type, column.length) for column in schema.columns] == [
        ("codigo", _DataType.INT, 0),
        ("nombre", _DataType.VARCHAR, 20),
        ("promedio", _DataType.DOUBLE, 0),
        ("activo", _DataType.BOOL, 0),
        ("ingreso", _DataType.DATE, 0),
    ]


def test_insert_convierte_los_cinco_tipos_incluida_date(
    database: _FakeDatabase,
    processor: QueryProcessor,
) -> None:
    processor.execute(
        "CREATE TABLE alumnos (codigo INT PRIMARY KEY, nombre VARCHAR(20), "
        "promedio DOUBLE, activo BOOL, ingreso DATE) USING HEAP"
    )

    result = processor.execute(
        "INSERT INTO alumnos VALUES (7, 'Ada', 18.5, TRUE, DATE '2024-02-29')"
    )

    expected_days = (date(2024, 2, 29) - date(1970, 1, 1)).days
    table = database.tables["alumnos"]
    assert table.records == [[7, "Ada", 18.5, True, _Date(expected_days)]]
    assert result.columns == ()
    assert result.rows == ()
    assert result.affected_rows == 1


def test_tabla_duplicada_se_traduce_y_conserva_schema_error(
    processor: QueryProcessor,
) -> None:
    sql = "CREATE TABLE datos (id INT PRIMARY KEY) USING HEAP"
    processor.execute(sql)

    with pytest.raises(SQLSemanticError, match="ya existe") as captured:
        processor.execute(sql)

    assert isinstance(captured.value.__cause__, _SchemaError)


def test_insert_en_tabla_inexistente_se_traduce_y_conserva_schema_error(
    processor: QueryProcessor,
) -> None:
    with pytest.raises(SQLSemanticError, match="inexistente") as captured:
        processor.execute("INSERT INTO ausente VALUES (1)")

    assert isinstance(captured.value.__cause__, _SchemaError)


@pytest.mark.parametrize("native_error_type", [_InvalidRecord, _DuplicateKey])
def test_errores_nativos_de_insert_se_traducen_con_causa(
    database: _FakeDatabase,
    processor: QueryProcessor,
    native_error_type: type[_NativeError],
) -> None:
    processor.execute("CREATE TABLE datos (id INT PRIMARY KEY) USING HEAP")
    native_error = native_error_type("fallo nativo inyectado")
    database.tables["datos"].fail_insert = native_error

    with pytest.raises(SQLSemanticError, match="fallo nativo inyectado") as captured:
        processor.execute("INSERT INTO datos VALUES (1)")

    assert captured.value.__cause__ is native_error


def test_pk_duplicada_no_toca_indices_existentes(
    database: _FakeDatabase,
    processor: QueryProcessor,
) -> None:
    processor.execute("CREATE TABLE datos (id INT PRIMARY KEY, nombre VARCHAR(20)) USING HEAP")
    index = database.add_index("datos", "por_nombre", 1)
    processor.execute("INSERT INTO datos VALUES (1, 'Ada')")

    with pytest.raises(SQLSemanticError) as captured:
        processor.execute("INSERT INTO datos VALUES (1, 'Grace')")

    assert isinstance(captured.value.__cause__, _DuplicateKey)
    assert index.insert_calls == [("Ada", _RID(1, 0))]
    assert database.tables["datos"].records == [[1, "Ada"]]


def test_fallo_al_abrir_indice_ocurre_antes_de_insertar(
    database: _FakeDatabase,
    processor: QueryProcessor,
) -> None:
    processor.execute("CREATE TABLE datos (id INT PRIMARY KEY, nombre VARCHAR(20)) USING HEAP")
    database.add_index("datos", "por_nombre", 1)
    native_error = _SchemaError("indice inaccesible")
    database.fail_index_open = native_error

    with pytest.raises(SQLSemanticError) as captured:
        processor.execute("INSERT INTO datos VALUES (1, 'Ada')")

    assert captured.value.__cause__ is native_error
    assert database.tables["datos"].insert_calls == []


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM datos",
        "DELETE FROM datos WHERE id = 1",
    ],
)
def test_select_y_delete_se_rechazan_hasta_sus_issues(
    database: _FakeDatabase,
    processor: QueryProcessor,
    sql: str,
) -> None:
    with pytest.raises(SQLUnsupportedError):
        processor.execute(sql)

    assert database.create_calls == []


def test_insert_mantiene_todos_los_indices_existentes(
    database: _FakeDatabase,
    processor: QueryProcessor,
) -> None:
    processor.execute(
        "CREATE TABLE alumnos (codigo INT PRIMARY KEY, nombre VARCHAR(20), ingreso DATE) USING HEAP"
    )
    by_name = database.add_index("alumnos", "por_nombre", 1)
    by_date = database.add_index("alumnos", "por_ingreso", 2)

    processor.execute("INSERT INTO alumnos VALUES (1, 'Ada', DATE '2024-02-29')")

    expected_rid = _RID(1, 0)
    expected_date = _Date((date(2024, 2, 29) - date(1970, 1, 1)).days)
    assert by_name.entries == [("Ada", expected_rid)]
    assert by_date.entries == [(expected_date, expected_rid)]


def test_fallo_de_indice_revierte_entradas_y_registro_insertados(
    database: _FakeDatabase,
    processor: QueryProcessor,
) -> None:
    processor.execute(
        "CREATE TABLE alumnos (codigo INT PRIMARY KEY, nombre VARCHAR(20), activo BOOL) USING HEAP"
    )
    by_name = database.add_index("alumnos", "por_nombre", 1)
    by_active = database.add_index("alumnos", "por_activo", 2)
    native_error = _InvalidRecord("fallo al mantener por_activo")
    by_active.fail_insert = native_error

    with pytest.raises(SQLSemanticError, match="fallo al mantener por_activo") as captured:
        processor.execute("INSERT INTO alumnos VALUES (1, 'Ada', TRUE)")

    rid = _RID(1, 0)
    assert captured.value.__cause__ is native_error
    assert by_name.insert_calls == [("Ada", rid)]
    assert by_name.remove_calls == [("Ada", rid)]
    assert by_name.entries == []
    assert by_active.insert_calls == [(True, rid)]
    assert by_active.remove_calls == [(True, rid)]
    assert database.tables["alumnos"].remove_calls == [1]
    assert database.tables["alumnos"].records == []


def test_io_de_indice_se_revierte_y_se_propaga_sin_traducir(
    database: _FakeDatabase,
    processor: QueryProcessor,
) -> None:
    processor.execute(
        "CREATE TABLE alumnos (codigo INT PRIMARY KEY, nombre VARCHAR(20)) USING HEAP"
    )
    index = database.add_index("alumnos", "por_nombre", 1)
    native_error = _IoError("disco no disponible")
    index.fail_insert = native_error

    with pytest.raises(_IoError) as captured:
        processor.execute("INSERT INTO alumnos VALUES (1, 'Ada')")

    assert captured.value is native_error
    assert index.remove_calls == [("Ada", _RID(1, 0))]
    assert database.tables["alumnos"].records == []


def test_rollback_es_best_effort_y_no_oculta_el_error_original(
    database: _FakeDatabase,
    processor: QueryProcessor,
) -> None:
    processor.execute(
        "CREATE TABLE alumnos (codigo INT PRIMARY KEY, nombre VARCHAR(20), activo BOOL) USING HEAP"
    )
    by_name = database.add_index("alumnos", "por_nombre", 1)
    by_active = database.add_index("alumnos", "por_activo", 2)
    by_name.fail_remove = _SchemaError("fallo secundario durante rollback")
    original_error = _InvalidRecord("fallo original de indice")
    by_active.fail_insert = original_error

    with pytest.raises(SQLSemanticError, match="fallo original de indice") as captured:
        processor.execute("INSERT INTO alumnos VALUES (1, 'Ada', TRUE)")

    assert captured.value.__cause__ is original_error
    assert by_name.remove_calls == [("Ada", _RID(1, 0))]
    assert database.tables["alumnos"].remove_calls == [1]
    assert database.tables["alumnos"].records == []
