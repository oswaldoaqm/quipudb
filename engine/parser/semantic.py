"""Enlace semantico puro para CREATE TABLE, INSERT INTO y SELECT."""

from __future__ import annotations

import math
import re

from engine.parser.ast import (
    AggregateCall,
    BetweenCondition,
    BooleanLiteral,
    ColumnReference,
    ComparisonCondition,
    CreateTableStatement,
    DateLiteral,
    DoubleLiteral,
    InsertStatement,
    IntegerLiteral,
    Literal,
    SelectStatement,
    SqlTypeName,
    StorageKind,
    StringLiteral,
    Wildcard,
)
from engine.parser.bound_ast import (
    BoundBetweenCondition,
    BoundColumn,
    BoundColumnReference,
    BoundComparisonCondition,
    BoundCondition,
    BoundCreateTable,
    BoundInsertStatement,
    BoundSchema,
    BoundSelectStatement,
    BoundValue,
)
from engine.parser.errors import SQLSemanticError, SQLUnsupportedError
from engine.parser.span import Span

_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_MAX_IDENTIFIER_LENGTH = 64
_INT32_MIN = -(2**31)
_INT32_MAX = 2**31 - 1

# Estos valores reproducen el layout del core para su pagina por defecto:
# - kDefaultPageSize = 4096, catalog/types.hpp
# - Page::kHeaderSize = 8, storage/page.hpp
# - un byte de estado por slot, heap_file.hpp y sequential_file.hpp
# - SequentialFile::kBodyHeader = 4 y al menos dos slots por pagina.
_DEFAULT_PAGE_SIZE = 4096
_PAGE_HEADER_SIZE = 8
_SLOT_STATE_SIZE = 1
_SEQUENTIAL_BODY_HEADER_SIZE = 4
_HEAP_MAX_RECORD_SIZE = _DEFAULT_PAGE_SIZE - _PAGE_HEADER_SIZE - _SLOT_STATE_SIZE
_SEQUENTIAL_MAX_RECORD_SIZE = (
    _DEFAULT_PAGE_SIZE - _PAGE_HEADER_SIZE - _SEQUENTIAL_BODY_HEADER_SIZE
) // 2 - _SLOT_STATE_SIZE


def bind_create_table(
    statement: CreateTableStatement,
    source: str | None = None,
) -> BoundCreateTable:
    """Valida un CREATE TABLE y construye el esquema fisico para el core."""

    _validate_identifier(statement.table.name, "tabla", statement.table.span, source)
    if not statement.columns:
        _fail("la tabla debe declarar al menos una columna", statement.span, source)

    bound_columns: list[BoundColumn] = []
    seen: set[str] = set()
    primary_keys: list[int] = []

    for index, column in enumerate(statement.columns):
        _validate_identifier(column.name.name, "columna", column.name.span, source)
        if column.name.name in seen:
            _fail(
                f"columna repetida en {statement.table.name}: {column.name.name}",
                column.name.span,
                source,
            )
        seen.add(column.name.name)

        length = _validate_type(
            column.data_type.name, column.data_type.length, column.data_type.span, source
        )
        bound_columns.append(BoundColumn(column.name.name, column.data_type.name, length))
        if column.primary_key:
            primary_keys.append(index)

    if not primary_keys:
        _fail("CREATE TABLE requiere exactamente una PRIMARY KEY", statement.span, source)
    if len(primary_keys) > 1:
        duplicate_key = statement.columns[primary_keys[1]]
        _fail("CREATE TABLE solo admite una PRIMARY KEY", duplicate_key.span, source)

    schema = BoundSchema(statement.table.name, tuple(bound_columns), primary_keys[0])
    limit = (
        _HEAP_MAX_RECORD_SIZE
        if statement.storage is StorageKind.HEAP
        else _SEQUENTIAL_MAX_RECORD_SIZE
    )
    if schema.record_size > limit:
        overflow_span = _first_overflow_span(statement, bound_columns, limit)
        _fail(
            f"el registro de {schema.record_size} bytes supera el maximo de {limit} bytes "
            f"para {statement.storage.value} con paginas de {_DEFAULT_PAGE_SIZE} bytes",
            overflow_span,
            source,
        )

    return BoundCreateTable(schema, statement.storage, statement.span)


def bind_insert(
    statement: InsertStatement,
    schema: BoundSchema,
    source: str | None = None,
) -> BoundInsertStatement:
    """Comprueba un INSERT posicional y convierte sus valores al esquema."""

    if statement.table.name != schema.table_name:
        _fail(
            f"el INSERT apunta a {statement.table.name}, pero el esquema es de {schema.table_name}",
            statement.table.span,
            source,
        )
    if len(statement.values) != len(schema.columns):
        _fail(
            f"INSERT tiene {len(statement.values)} valores y la tabla {schema.table_name} "
            f"requiere {len(schema.columns)}",
            statement.span,
            source,
        )

    values = tuple(
        _bind_value(literal, column, source)
        for literal, column in zip(statement.values, schema.columns, strict=True)
    )
    return BoundInsertStatement(statement.table.name, values, statement.span)


def bind_select(
    statement: SelectStatement,
    schema: BoundSchema,
    source: str | None = None,
) -> BoundSelectStatement:
    """Resuelve una consulta SELECT sin acceder al catalogo ni al core nativo."""

    if statement.table.name != schema.table_name:
        _fail(
            f"el SELECT apunta a {statement.table.name}, pero el esquema es de {schema.table_name}",
            statement.table.span,
            source,
        )

    if statement.group_by is not None:
        raise SQLUnsupportedError(
            "GROUP BY se implementa en el issue #28",
            statement.group_by.span,
            source,
        )
    if statement.order_by is not None:
        raise SQLUnsupportedError(
            "ORDER BY se implementa en el issue #28",
            statement.order_by.span,
            source,
        )

    wildcard = any(isinstance(projection, Wildcard) for projection in statement.projections)
    if wildcard:
        if len(statement.projections) != 1:
            wildcard_projection = next(
                projection
                for projection in statement.projections
                if isinstance(projection, Wildcard)
            )
            _fail(
                "'*' no se puede combinar con otras proyecciones",
                wildcard_projection.span,
                source,
            )
        wildcard_span = statement.projections[0].span
        projections = tuple(
            BoundColumnReference(index, column, wildcard_span)
            for index, column in enumerate(schema.columns)
        )
    else:
        projections = tuple(
            _bind_projection(projection, schema, source) for projection in statement.projections
        )

    if not statement.projections:
        _fail("SELECT requiere al menos una proyeccion", statement.span, source)

    where = _bind_condition(statement.where, schema, source)
    return BoundSelectStatement(schema, projections, wildcard, where, statement.span)


def _bind_projection(
    projection: ColumnReference | AggregateCall,
    schema: BoundSchema,
    source: str | None,
) -> BoundColumnReference:
    if isinstance(projection, AggregateCall):
        raise SQLUnsupportedError(
            "las funciones de agregado se implementan en el issue #28",
            projection.span,
            source,
        )
    return _resolve_column(projection, schema, source)


def _bind_condition(
    condition: ComparisonCondition | BetweenCondition | None,
    schema: BoundSchema,
    source: str | None,
) -> BoundCondition | None:
    if condition is None:
        return None

    column = _resolve_column(condition.column, schema, source)
    if isinstance(condition, ComparisonCondition):
        value = _bind_predicate_value(condition.value, column.column, source)
        return BoundComparisonCondition(column, condition.operator, value, condition.span)

    lower = _bind_predicate_value(condition.lower, column.column, source)
    upper = _bind_predicate_value(condition.upper, column.column, source)
    return BoundBetweenCondition(column, lower, upper, condition.span)


def _resolve_column(
    reference: ColumnReference,
    schema: BoundSchema,
    source: str | None,
) -> BoundColumnReference:
    for index, column in enumerate(schema.columns):
        if reference.name.name == column.name:
            return BoundColumnReference(index, column, reference.span)
    _fail(
        f"la columna {reference.name.name!r} no existe en la tabla {schema.table_name!r}",
        reference.name.span,
        source,
    )


def _validate_identifier(name: str, role: str, span: Span, source: str | None) -> None:
    if len(name) > _MAX_IDENTIFIER_LENGTH:
        _fail(
            f"nombre de {role} demasiado largo: el maximo es {_MAX_IDENTIFIER_LENGTH}",
            span,
            source,
        )
    if _IDENTIFIER.fullmatch(name) is None:
        _fail(
            f"nombre de {role} invalido: {name!r}; use [A-Za-z_][A-Za-z0-9_]*",
            span,
            source,
        )


def _validate_type(
    data_type: SqlTypeName,
    length: int | None,
    span: Span,
    source: str | None,
) -> int | None:
    if data_type is SqlTypeName.VARCHAR:
        if length is None or length <= 0:
            _fail("VARCHAR necesita una longitud mayor que 0", span, source)
        return length
    if length is not None:
        _fail(f"solo VARCHAR admite longitud; {data_type.value} no la usa", span, source)
    return None


def _first_overflow_span(
    statement: CreateTableStatement,
    columns: list[BoundColumn],
    limit: int,
) -> Span:
    size = 0
    for definition, column in zip(statement.columns, columns, strict=True):
        size += column.byte_size
        if size > limit:
            return definition.data_type.span
    return statement.span


def _bind_value(literal: Literal, column: BoundColumn, source: str | None) -> BoundValue:
    if column.data_type is SqlTypeName.INT:
        return _bind_int(literal, column, source)
    if column.data_type is SqlTypeName.DOUBLE:
        return _bind_double(literal, column, source)
    if column.data_type is SqlTypeName.VARCHAR:
        return _bind_varchar(literal, column, source)
    if column.data_type is SqlTypeName.BOOL:
        if not isinstance(literal, BooleanLiteral):
            _wrong_type(literal, column, source)
        return literal.value
    if column.data_type is SqlTypeName.DATE:
        if not isinstance(literal, DateLiteral):
            _wrong_type(literal, column, source)
        return literal.value
    raise AssertionError(f"tipo SQL desconocido: {column.data_type!r}")


def _bind_predicate_value(
    literal: Literal,
    column: BoundColumn,
    source: str | None,
) -> BoundValue:
    """Convierte una clave de consulta sin imponer limites de almacenamiento.

    Un texto mayor que ``VARCHAR(n)`` no puede estar guardado en la columna,
    pero sigue siendo una clave comparable y puede producir cero filas o
    delimitar un rango. La aridad y capacidad solo se exigen al insertar.
    """

    if column.data_type is not SqlTypeName.VARCHAR:
        return _bind_value(literal, column, source)
    if not isinstance(literal, StringLiteral):
        _wrong_type(literal, column, source)
    try:
        literal.value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise SQLSemanticError(
            f"columna {column.name}: el texto no se puede codificar como UTF-8",
            literal.span,
            source,
        ) from exc
    return literal.value


def _bind_int(literal: Literal, column: BoundColumn, source: str | None) -> int:
    if not isinstance(literal, IntegerLiteral):
        _wrong_type(literal, column, source)
    if not _INT32_MIN <= literal.value <= _INT32_MAX:
        _fail(
            f"columna {column.name}: {literal.value} esta fuera del rango INT de 32 bits",
            literal.span,
            source,
        )
    return literal.value


def _bind_double(literal: Literal, column: BoundColumn, source: str | None) -> float:
    if not isinstance(literal, (IntegerLiteral, DoubleLiteral)):
        _wrong_type(literal, column, source)
    try:
        value = float(literal.value)
    except (OverflowError, ValueError) as exc:
        raise SQLSemanticError(
            f"columna {column.name}: el valor desborda DOUBLE",
            literal.span,
            source,
        ) from exc
    if not math.isfinite(value):
        _fail(f"columna {column.name}: DOUBLE requiere un valor finito", literal.span, source)
    return value


def _bind_varchar(literal: Literal, column: BoundColumn, source: str | None) -> str:
    if not isinstance(literal, StringLiteral):
        _wrong_type(literal, column, source)
    if "\0" in literal.value:
        _fail(f"columna {column.name}: VARCHAR no admite bytes nulos", literal.span, source)
    try:
        byte_length = len(literal.value.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise SQLSemanticError(
            f"columna {column.name}: el texto no se puede codificar como UTF-8",
            literal.span,
            source,
        ) from exc
    if column.length is None:
        _fail(f"columna {column.name}: el esquema VARCHAR no tiene longitud", literal.span, source)
    if byte_length > column.length:
        _fail(
            f"columna {column.name}: el texto ocupa {byte_length} bytes y supera "
            f"VARCHAR({column.length})",
            literal.span,
            source,
        )
    return literal.value


def _wrong_type(literal: Literal, column: BoundColumn, source: str | None) -> None:
    _fail(
        f"columna {column.name}: se esperaba {column.data_type.value} y se recibio "
        f"{_literal_type(literal)}",
        literal.span,
        source,
    )


def _literal_type(literal: Literal) -> str:
    if isinstance(literal, IntegerLiteral):
        return "INT"
    if isinstance(literal, DoubleLiteral):
        return "DOUBLE"
    if isinstance(literal, StringLiteral):
        return "VARCHAR"
    if isinstance(literal, BooleanLiteral):
        return "BOOL"
    if isinstance(literal, DateLiteral):
        return "DATE"
    return type(literal).__name__


def _fail(message: str, span: Span, source: str | None) -> None:
    raise SQLSemanticError(message, span, source)


__all__ = ["bind_create_table", "bind_insert", "bind_select"]
