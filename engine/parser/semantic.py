"""Enlace semantico puro para CREATE TABLE, INSERT, SELECT y DELETE."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass

from engine.parser.ast import (
    AggregateCall,
    AggregateFunction,
    BetweenCondition,
    BooleanLiteral,
    ColumnReference,
    ComparisonCondition,
    CreateTableStatement,
    DateLiteral,
    DeleteStatement,
    DoubleLiteral,
    FromSource,
    InsertStatement,
    IntegerLiteral,
    Literal,
    SelectStatement,
    SqlTypeName,
    StorageKind,
    StringLiteral,
    TableRef,
    Wildcard,
)
from engine.parser.bound_ast import (
    BoundAggregateCall,
    BoundBetweenCondition,
    BoundColumn,
    BoundColumnReference,
    BoundComparisonCondition,
    BoundCondition,
    BoundCreateTable,
    BoundDeleteStatement,
    BoundGroupBy,
    BoundInsertStatement,
    BoundJoinRef,
    BoundOrderBy,
    BoundProjection,
    BoundSchema,
    BoundSelectStatement,
    BoundSource,
    BoundTableRef,
    BoundValue,
    join_output_schema,
)
from engine.parser.errors import SQLSemanticError
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


@dataclass(frozen=True, slots=True)
class _Scope:
    """Que nombres ve la consulta y en que posicion de su esquema de salida.

    ``visible`` guarda la tabla y el nombre ORIGINAL de cada columna, antes de
    que ``join_output_schema`` prefije las que colisionan. Es lo que permite
    resolver ``alumnos.codigo`` sin depender de como quedo escrita la cabecera.
    """

    schema: BoundSchema
    visible: tuple[tuple[str, str, int], ...]

    @classmethod
    def of(cls, source: BoundSource) -> _Scope:
        return cls(source.schema, tuple(_visible_columns(source)))


def _visible_columns(source: BoundSource, offset: int = 0) -> list[tuple[str, str, int]]:
    if isinstance(source, BoundTableRef):
        return [
            (source.schema.table_name, column.name, offset + index)
            for index, column in enumerate(source.schema.columns)
        ]
    return _visible_columns(source.left, offset) + _visible_columns(
        source.right, offset + len(source.left.schema.columns)
    )


def _bind_source(
    node: FromSource,
    catalogo: Mapping[str, BoundSchema],
    source: str | None,
) -> BoundSource:
    if isinstance(node, TableRef):
        schema = catalogo.get(node.table.name)
        if schema is None:
            conocidas = ", ".join(sorted(catalogo)) or "ninguna"
            _fail(
                f"la consulta apunta a {node.table.name}, pero los esquemas "
                f"disponibles son: {conocidas}",
                node.table.span,
                source,
            )
            raise AssertionError  # pragma: no cover - _fail siempre lanza
        return BoundTableRef(schema, node.span)

    left = _bind_source(node.left, catalogo, source)
    right = _bind_source(node.right, catalogo, source)
    izquierdo = _Scope.of(left)
    derecho = _Scope.of(right)

    # El orden de los operandos del ON no importa: `ON b.x = a.y` es tan valido
    # como `ON a.y = b.x`. Se prueba el emparejamiento directo y, si no cuadra,
    # el cruzado; si tampoco, se deja que _resolve_column explique por que.
    directo = (
        _lookup(node.left_column, izquierdo),
        _lookup(node.right_column, derecho),
    )
    cruzado = (
        _lookup(node.right_column, izquierdo),
        _lookup(node.left_column, derecho),
    )
    if None not in directo:
        left_column, right_column = directo
    elif None not in cruzado:
        left_column, right_column = cruzado
    else:
        left_column = _resolve_column(node.left_column, izquierdo, source)
        right_column = _resolve_column(node.right_column, derecho, source)
    assert left_column is not None and right_column is not None

    if left_column.column.data_type is not right_column.column.data_type:
        _fail(
            f"no se puede juntar {left_column.column.name} "
            f"({left_column.column.data_type.value}) con {right_column.column.name} "
            f"({right_column.column.data_type.value})",
            node.span,
            source,
        )

    return BoundJoinRef(
        left=left,
        right=right,
        left_column=left_column,
        right_column=right_column,
        schema=join_output_schema(left.schema, right.schema),
        span=node.span,
    )


def bind_select(
    statement: SelectStatement,
    schemas: BoundSchema | Mapping[str, BoundSchema],
    source: str | None = None,
) -> BoundSelectStatement:
    """Resuelve una consulta SELECT sin acceder al catalogo ni al core nativo.

    ``schemas`` admite un solo ``BoundSchema`` -- la forma de siempre, para una
    consulta de una tabla -- o un mapa de nombre a esquema, que es lo que un
    ``JOIN`` necesita.
    """

    catalogo: Mapping[str, BoundSchema] = (
        {schemas.table_name: schemas} if isinstance(schemas, BoundSchema) else schemas
    )
    bound_source = _bind_source(statement.source, catalogo, source)
    scope = _Scope.of(bound_source)
    schema = scope.schema

    if not statement.projections:
        _fail("SELECT requiere al menos una proyeccion", statement.span, source)

    first_aggregate = next(
        (
            projection
            for projection in statement.projections
            if isinstance(projection, AggregateCall)
        ),
        None,
    )
    if statement.group_by is None and first_aggregate is not None:
        _fail(
            "las funciones de agregado requieren una clausula GROUP BY",
            first_aggregate.span,
            source,
        )

    group_by = None
    if statement.group_by is not None:
        group_by = BoundGroupBy(
            _resolve_column(statement.group_by.column, scope, source),
            statement.group_by.span,
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
        if group_by is not None:
            _fail(
                "'*' no se admite en una consulta con GROUP BY",
                statement.projections[0].span,
                source,
            )
        wildcard_span = statement.projections[0].span
        projections: tuple[BoundProjection, ...] = tuple(
            BoundColumnReference(index, column, wildcard_span)
            for index, column in enumerate(schema.columns)
        )
    else:
        projections = tuple(
            _bind_projection(projection, scope, source) for projection in statement.projections
        )

    if group_by is not None:
        if not any(isinstance(projection, BoundAggregateCall) for projection in projections):
            _fail(
                "GROUP BY requiere al menos una funcion de agregado",
                statement.group_by.span,  # type: ignore[union-attr]
                source,
            )
        for projection in projections:
            if (
                isinstance(projection, BoundColumnReference)
                and projection.index != group_by.column.index
            ):
                _fail(
                    "toda columna proyectada sin agregar debe ser la columna de GROUP BY",
                    projection.span,
                    source,
                )

    order_by = None
    if statement.order_by is not None:
        order_column = _resolve_column(statement.order_by.column, scope, source)
        if group_by is not None and order_column.index != group_by.column.index:
            _fail(
                "ORDER BY solo puede usar la columna de GROUP BY en una consulta agrupada",
                statement.order_by.column.span,
                source,
            )
        order_by = BoundOrderBy(
            order_column,
            statement.order_by.direction,
            statement.order_by.span,
        )

    where = _bind_condition(statement.where, scope, source)
    return BoundSelectStatement(
        source=bound_source,
        projections=projections,
        wildcard=wildcard,
        where=where,
        group_by=group_by,
        order_by=order_by,
        span=statement.span,
    )


def bind_delete(
    statement: DeleteStatement,
    schema: BoundSchema,
    source: str | None = None,
) -> BoundDeleteStatement:
    """Resuelve la tabla y la condicion obligatoria de un ``DELETE``."""

    if statement.table.name != schema.table_name:
        _fail(
            f"el DELETE apunta a {statement.table.name}, pero el esquema es de {schema.table_name}",
            statement.table.span,
            source,
        )

    where = _bind_condition(statement.where, _Scope.of(BoundTableRef(schema, statement.span)), source)
    if where is None:
        # El parser no construye este caso; protege AST creados a mano.
        _fail("DELETE requiere una clausula WHERE", statement.span, source)
    return BoundDeleteStatement(schema, where, statement.span)


def _bind_projection(
    projection: ColumnReference | AggregateCall,
    scope: _Scope,
    source: str | None,
) -> BoundProjection:
    if isinstance(projection, ColumnReference):
        return _resolve_column(projection, scope, source)

    if projection.function is AggregateFunction.COUNT:
        if not isinstance(projection.argument, Wildcard):
            _fail("COUNT requiere '*' como argumento", projection.argument.span, source)
        return BoundAggregateCall(projection.function, None, projection.span)

    if isinstance(projection.argument, Wildcard):
        _fail("solo COUNT admite '*' como argumento", projection.argument.span, source)
    argument = _resolve_column(projection.argument, scope, source)
    if projection.function in {
        AggregateFunction.SUM,
        AggregateFunction.AVG,
    } and argument.column.data_type not in {SqlTypeName.INT, SqlTypeName.DOUBLE}:
        _fail(
            f"{projection.function.value} requiere una columna INT o DOUBLE; "
            f"{argument.column.name} es {argument.column.data_type.value}",
            projection.argument.span,
            source,
        )
    return BoundAggregateCall(projection.function, argument, projection.span)


def _bind_condition(
    condition: ComparisonCondition | BetweenCondition | None,
    scope: _Scope,
    source: str | None,
) -> BoundCondition | None:
    if condition is None:
        return None

    column = _resolve_column(condition.column, scope, source)
    if isinstance(condition, ComparisonCondition):
        value = _bind_predicate_value(condition.value, column.column, source)
        return BoundComparisonCondition(column, condition.operator, value, condition.span)

    lower = _bind_predicate_value(condition.lower, column.column, source)
    upper = _bind_predicate_value(condition.upper, column.column, source)
    return BoundBetweenCondition(column, lower, upper, condition.span)


def _lookup(reference: ColumnReference, scope: _Scope) -> BoundColumnReference | None:
    """Busca una columna sin explicar el fallo. ``None`` si no hay exactamente una."""

    nombre = reference.name.name
    if reference.qualifier is not None:
        posiciones = [
            index
            for tabla, columna, index in scope.visible
            if tabla == reference.qualifier.name and columna == nombre
        ]
    else:
        posiciones = [index for _, columna, index in scope.visible if columna == nombre]

    if len(posiciones) != 1:
        return None
    index = posiciones[0]
    return BoundColumnReference(index, scope.schema.columns[index], reference.span)


def _resolve_column(
    reference: ColumnReference,
    scope: _Scope,
    source: str | None,
) -> BoundColumnReference:
    """Resuelve una columna a su posicion en el esquema de salida del ``FROM``.

    Una referencia calificada solo mira la tabla que la califica; una sin
    calificar mira todo el alcance y es un error si aparece en mas de un lado.
    """

    nombre = reference.name.name
    if reference.qualifier is not None:
        calificador = reference.qualifier.name
        tablas = {tabla for tabla, _, _ in scope.visible}
        if calificador not in tablas:
            disponibles = ", ".join(sorted(tablas))
            _fail(
                f"el calificador {calificador!r} no corresponde a ninguna tabla de la "
                f"consulta; hay: {disponibles}",
                reference.qualifier.span,
                source,
            )
        posiciones = [
            index for tabla, columna, index in scope.visible
            if tabla == calificador and columna == nombre
        ]
    else:
        posiciones = [index for _, columna, index in scope.visible if columna == nombre]

    if len(posiciones) > 1:
        duenas = ", ".join(
            sorted(tabla for tabla, columna, _ in scope.visible if columna == nombre)
        )
        _fail(
            f"la columna {nombre!r} es ambigua: esta en {duenas}; califica cual quieres",
            reference.name.span,
            source,
        )
    if posiciones:
        index = posiciones[0]
        return BoundColumnReference(index, scope.schema.columns[index], reference.span)

    donde = (
        f"la tabla {reference.qualifier.name!r}"
        if reference.qualifier is not None
        else f"la consulta sobre {scope.schema.table_name!r}"
    )
    _fail(
        f"la columna {nombre!r} no existe en {donde}",
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


__all__ = ["bind_create_table", "bind_delete", "bind_insert", "bind_select"]
