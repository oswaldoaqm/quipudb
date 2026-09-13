"""Conversion entre el modelo SQL puro y los objetos de ``quipudb_native``."""

from __future__ import annotations

import importlib
from datetime import date
from types import ModuleType
from typing import Any

from engine.parser.ast import SqlTypeName
from engine.parser.bound_ast import BoundColumn, BoundSchema, BoundValue

_EPOCH = date(1970, 1, 1)


def load_native() -> ModuleType:
    """Carga los bindings solo cuando se construye el procesador.

    Mantener este import fuera del nivel de modulo permite usar el lexer, el
    parser y el analisis semantico sin haber compilado C++.
    """

    try:
        return importlib.import_module("quipudb_native")
    except ModuleNotFoundError as error:
        if error.name != "quipudb_native":
            raise
        raise RuntimeError(
            "quipudb_native no esta compilado; configure CMake con -DQUIPUDB_BUILD_PYTHON=ON"
        ) from error


def to_native_schema(schema: BoundSchema, native: Any) -> Any:
    """Construye una copia nativa del esquema ya validado."""

    data_types = {
        SqlTypeName.INT: native.DataType.INT,
        SqlTypeName.DOUBLE: native.DataType.DOUBLE,
        SqlTypeName.VARCHAR: native.DataType.VARCHAR,
        SqlTypeName.BOOL: native.DataType.BOOL,
        SqlTypeName.DATE: native.DataType.DATE,
    }
    columns = [
        native.Column(column.name, data_types[column.data_type], column.length or 0)
        for column in schema.columns
    ]
    return native.Schema(schema.table_name, columns, schema.key_column)


def from_native_schema(schema: Any, native: Any) -> BoundSchema:
    """Copia metadata del catalogo nativo al modelo semantico puro."""

    data_types = (
        (native.DataType.INT, SqlTypeName.INT),
        (native.DataType.DOUBLE, SqlTypeName.DOUBLE),
        (native.DataType.VARCHAR, SqlTypeName.VARCHAR),
        (native.DataType.BOOL, SqlTypeName.BOOL),
        (native.DataType.DATE, SqlTypeName.DATE),
    )
    columns: list[BoundColumn] = []
    for column in schema.columns:
        sql_type = next(
            (sql_type for native_type, sql_type in data_types if column.type == native_type),
            None,
        )
        if sql_type is None:
            raise RuntimeError(f"tipo nativo desconocido en la columna {column.name!r}")
        length = int(column.length) if sql_type is SqlTypeName.VARCHAR else None
        columns.append(BoundColumn(str(column.name), sql_type, length))

    return BoundSchema(
        table_name=str(schema.table_name),
        columns=tuple(columns),
        key_column=int(schema.key_column),
    )


def to_native_values(values: tuple[BoundValue, ...], native: Any) -> list[object]:
    """Convierte fechas; los otros cuatro tipos ya tienen forma nativa."""

    converted: list[object] = []
    for value in values:
        if isinstance(value, date) and not isinstance(value, bool):
            converted.append(native.Date((value - _EPOCH).days))
        else:
            converted.append(value)
    return converted
