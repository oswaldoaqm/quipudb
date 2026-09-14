"""Conversion entre el modelo SQL puro y los objetos de ``quipudb_native``."""

from __future__ import annotations

import importlib
from datetime import date, timedelta
from types import ModuleType
from typing import Any

from engine.parser.ast import SqlTypeName
from engine.parser.bound_ast import BoundColumn, BoundSchema, BoundValue

_EPOCH = date(1970, 1, 1)
_INT32_MIN = -(2**31)
_INT32_MAX = 2**31 - 1


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

    return [to_native_value(value, native) for value in values]


def to_native_value(value: BoundValue, native: Any) -> object:
    """Convierte un escalar SQL al ``Value`` que espera pybind11."""

    if isinstance(value, date) and not isinstance(value, bool):
        return native.Date((value - _EPOCH).days)
    return value


def from_native_record(record: Any, schema: BoundSchema) -> tuple[BoundValue, ...]:
    """Copia un registro nativo y convierte sus fechas a ``datetime.date``."""

    if len(record) != len(schema.columns):
        raise RuntimeError(
            f"el core devolvio {len(record)} columnas para un esquema de {len(schema.columns)}"
        )

    converted: list[BoundValue] = []
    for value, column in zip(record, schema.columns, strict=True):
        if column.data_type is SqlTypeName.DATE:
            if isinstance(value, date) and not isinstance(value, bool):
                converted.append(value)
                continue
            try:
                days = int(value.days)
                converted.append(_EPOCH + timedelta(days=days))
            except (AttributeError, OverflowError, TypeError, ValueError) as error:
                raise RuntimeError(
                    f"el core devolvio una fecha invalida en la columna {column.name!r}"
                ) from error
            continue
        converted.append(value)
    return tuple(converted)


def native_range_bounds(column: BoundColumn, native: Any) -> tuple[object, object]:
    """Limites inclusivos del dominio fisico de una columna.

    ``TableFile.range_search`` e ``Index.range_search`` solo aceptan rangos
    cerrados. Estos extremos permiten representar ``<``, ``<=``, ``>`` y
    ``>=`` sin sumar o restar uno al literal (lo que desbordaria INT o DATE).
    Los operadores estrictos conservan despues un filtro residual.
    """

    if column.data_type is SqlTypeName.INT:
        return _INT32_MIN, _INT32_MAX
    if column.data_type is SqlTypeName.DOUBLE:
        return float("-inf"), float("inf")
    if column.data_type is SqlTypeName.VARCHAR:
        if column.length is None:
            raise ValueError("VARCHAR necesita una longitud para construir su rango")
        # Todo VARCHAR que cruza pybind11 es UTF-8 valido y ocupa como maximo
        # ``length`` bytes. Esta construccion codifica exactamente esa cantidad
        # y elige, de izquierda a derecha, el mayor code point que cabe.
        tail = {0: "", 1: "\x7f", 2: "\u07ff", 3: "\uffff"}
        upper = "\U0010ffff" * (column.length // 4) + tail[column.length % 4]
        return "", upper
    if column.data_type is SqlTypeName.BOOL:
        return False, True
    if column.data_type is SqlTypeName.DATE:
        return native.Date(_INT32_MIN), native.Date(_INT32_MAX)
    raise AssertionError(f"tipo SQL desconocido: {column.data_type!r}")


__all__ = [
    "from_native_record",
    "from_native_schema",
    "load_native",
    "native_range_bounds",
    "to_native_schema",
    "to_native_value",
    "to_native_values",
]
