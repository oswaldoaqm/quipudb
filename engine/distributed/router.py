"""Enrutamiento de ``POST /query`` en el coordinador (#279).

Decide, sin red ni disco, a qué fragmentos va una sentencia y qué SQL recibe
cada uno. Usa el parser y el binder del motor, así que los errores léxicos,
sintácticos y semánticos salen con el mismo texto y la misma ubicación que en
la API local. Lo que v1 no admite sale como ``SQLUnsupportedError`` con el
prefijo ``[UNSUPPORTED_DISTRIBUTED]``.

Hay dos destinos posibles:

- ``Execute``: un solo fragmento recibe el SQL original. Es el caso de toda
  tabla ``single``, del INSERT en una ``range`` (va al dueño de la clave) y del
  DELETE por igualdad de clave primaria.
- ``Scan``: un SELECT sobre una tabla ``range``. Cada fragmento que puede tener
  filas recibe un ``SELECT * FROM tabla [WHERE ...]`` generado desde el AST; el
  orden, la proyección y el límite los aplica el coordinador al combinar
  (``merge.py``).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal

from engine.distributed.catalog import DistributedCatalog
from engine.distributed.contracts import DistributedTable, Fragment
from engine.distributed.partitioning import fragments_for_comparison, owner_for_key
from engine.parser import parse_sql_script
from engine.parser.ast import (
    BooleanLiteral,
    ComparisonCondition,
    DateLiteral,
    DeleteStatement,
    DoubleLiteral,
    ExplainStatement,
    InsertStatement,
    IntegerLiteral,
    JoinRef,
    OrderBy,
    OrderDirection,
    SelectStatement,
    StringLiteral,
)
from engine.parser.bound_ast import (
    BoundColumnReference,
    BoundComparisonCondition,
    SqlTypeName,
)
from engine.parser.errors import SQLSemanticError, SQLUnsupportedError
from engine.parser.semantic import bind_delete, bind_insert, bind_select
from engine.parser.span import Span

SCALAR_TYPES = frozenset(
    {SqlTypeName.INT, SqlTypeName.DOUBLE, SqlTypeName.VARCHAR, SqlTypeName.BOOL, SqlTypeName.DATE}
)
"""Tipos que admite el WHERE de un scan; POINT y VECTOR quedan fuera en v1."""


@dataclass(frozen=True)
class Execute:
    """Una sentencia que resuelve un solo fragmento con el SQL original."""

    node_id: str
    fragment_id: str
    table: str
    sql: str
    write: bool


@dataclass(frozen=True)
class Scan:
    """Un SELECT sobre una tabla ``range``, repartido entre fragmentos.

    ``projection`` son las posiciones de las columnas pedidas dentro del orden
    físico; ``order`` es la dirección del ORDER BY por la clave primaria, si lo
    hay. ``fragments`` puede quedar vacío: ninguna clave cumple el predicado.
    """

    table: DistributedTable
    fragments: tuple[Fragment, ...]
    sql: str
    projection: tuple[int, ...]
    order: OrderDirection | None
    limit: int | None


def unsupported(message: str, span: Span, sql: str) -> SQLUnsupportedError:
    return SQLUnsupportedError(f"[UNSUPPORTED_DISTRIBUTED] {message}", span, sql)


def route(catalog: DistributedCatalog, sql: str) -> Execute | Scan:
    """Decide el destino de ``sql``. Lanza ``SQLError`` si no puede ejecutarse."""

    statements = parse_sql_script(sql)
    if len(statements) != 1:
        span = statements[1].span if len(statements) > 1 else Span(0, len(sql), 1, 1, 1, 1)
        raise unsupported("v1 admite exactamente una sentencia por consulta", span, sql)
    statement = statements[0]
    inner = statement.statement if isinstance(statement, ExplainStatement) else statement

    if isinstance(inner, SelectStatement):
        if isinstance(inner.source, JoinRef):
            raise unsupported("v1 no admite JOIN entre tablas distribuidas", inner.source.span, sql)
        name = inner.source.table
    elif isinstance(inner, (InsertStatement, DeleteStatement)):
        name = inner.table
    else:
        raise unsupported(
            "v1 no admite DDL ni transacciones: la topología del catálogo es estática",
            statement.span,
            sql,
        )

    try:
        table = catalog.table(name.name)
    except KeyError:
        raise SQLSemanticError(f"la tabla {name.name!r} no existe", name.span, sql) from None

    if table.mode == "single":
        return _route_single(table, inner, sql)
    if isinstance(statement, ExplainStatement):
        raise unsupported(
            "EXPLAIN no está admitido sobre una tabla range: ningún plan de un nodo "
            "describe la consulta completa",
            statement.span,
            sql,
        )
    if isinstance(statement, InsertStatement):
        bound = bind_insert(statement, table.bound_schema, sql)
        key = bound.values[table.bound_schema.key_column]
        return _execute(table, owner_for_key(table, key), sql, write=True)
    if isinstance(statement, DeleteStatement):
        return _route_delete(table, statement, sql)
    return _route_select(table, statement, sql)


def _execute(table: DistributedTable, fragment: Fragment, sql: str, *, write: bool) -> Execute:
    return Execute(fragment.node_id, fragment.fragment_id, table.name, sql, write)


def _route_single(table: DistributedTable, statement, sql: str) -> Execute:
    # El dueño ejecuta el SQL original y valida todo lo demás; su respuesta
    # vuelve sin cambios, plan incluido.
    (fragment,) = table.fragments
    write = isinstance(statement, (InsertStatement, DeleteStatement))
    return _execute(table, fragment, sql, write=write)


def _route_delete(table: DistributedTable, statement: DeleteStatement, sql: str) -> Execute:
    bound = bind_delete(statement, table.bound_schema, sql)
    where = bound.where
    if (
        not isinstance(where, BoundComparisonCondition)
        or where.operator != "="
        or where.column.index != table.bound_schema.key_column
    ):
        raise unsupported(
            f"DELETE sobre una tabla range solo admite WHERE {table.primary_key} = valor",
            statement.where.span,
            sql,
        )
    return _execute(table, owner_for_key(table, where.value), sql, write=True)


def _route_select(table: DistributedTable, statement: SelectStatement, sql: str) -> Scan:
    bound = bind_select(statement, {table.name: table.bound_schema}, sql)

    if bound.group_by is not None:
        raise unsupported("v1 no admite GROUP BY en una tabla range", statement.group_by.span, sql)
    for projection, original in zip(bound.projections, statement.projections, strict=False):
        if not isinstance(projection, BoundColumnReference):
            raise unsupported(
                "v1 solo admite columnas o * en un SELECT sobre una tabla range",
                original.span,
                sql,
            )
    if bound.score is not None:
        raise unsupported("v1 no admite SCORE en una tabla range", statement.span, sql)

    where = statement.where
    fragments = table.ordered_fragments
    if where is not None:
        if not isinstance(where, ComparisonCondition):
            raise unsupported(
                "en una tabla range el WHERE solo admite una comparación columna op valor",
                where.span,
                sql,
            )
        condition = bound.where
        if condition.column.column.data_type not in SCALAR_TYPES:
            raise unsupported(
                "el WHERE de una tabla range no admite columnas POINT ni VECTOR", where.span, sql
            )
        if condition.column.index == table.bound_schema.key_column:
            # La clave de fragmentación descarta los rangos que no pueden
            # tener filas: una igualdad va a un solo nodo.
            fragments = fragments_for_comparison(table, condition.operator.value, condition.value)

    order = None
    if statement.order_by is not None:
        if (
            not isinstance(statement.order_by, OrderBy)
            or bound.order_by.column.index != table.bound_schema.key_column
        ):
            raise unsupported(
                f"en una tabla range solo se puede ordenar por {table.primary_key}",
                statement.order_by.span,
                sql,
            )
        order = statement.order_by.direction

    if bound.wildcard:
        projection = tuple(range(len(table.columns)))
    else:
        projection = tuple(p.index for p in bound.projections)
    return Scan(
        table=table,
        fragments=tuple(fragments),
        sql=scan_sql(table.name, where),
        projection=projection,
        order=order,
        limit=bound.limit,
    )


def scan_sql(table: str, where: ComparisonCondition | None) -> str:
    """``SELECT * FROM tabla [WHERE columna op literal];`` armado desde el AST.

    No copia texto de la consulta del usuario: cada pieza se vuelve a escribir
    con la sintaxis del lexer, así que el nodo lee el mismo valor.
    """

    text = f"SELECT * FROM {table}"
    if where is not None:
        text += f" WHERE {where.column.name.name} {where.operator.value} {literal_sql(where.value)}"
    return text + ";"


def literal_sql(literal) -> str:
    """Un literal escalar en la sintaxis que acepta el lexer."""

    if isinstance(literal, BooleanLiteral):
        return "TRUE" if literal.value else "FALSE"
    if isinstance(literal, IntegerLiteral):
        return str(literal.value)
    if isinstance(literal, DoubleLiteral):
        return double_sql(literal.value)
    if isinstance(literal, StringLiteral):
        return "'" + literal.value.replace("'", "''") + "'"
    if isinstance(literal, DateLiteral):
        return f"DATE '{literal.value.isoformat()}'"
    raise TypeError(f"literal no escalar: {type(literal).__name__}")


def double_sql(value: float) -> str:
    """Un DOUBLE sin notación exponencial y con punto, como exige el lexer.

    ``repr`` da el decimal más corto que vuelve al mismo float; ``Decimal`` lo
    escribe sin exponente. Así el nodo obtiene exactamente el mismo valor.
    """

    if not math.isfinite(value):
        raise ValueError("un DOUBLE de SQL es finito")
    text = format(Decimal(repr(value)), "f")
    return text if "." in text else text + ".0"


__all__ = ["Execute", "Scan", "double_sql", "literal_sql", "route", "scan_sql", "unsupported"]
