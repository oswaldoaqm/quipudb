"""Arbol de sintaxis abstracta del subconjunto SQL de QuipuDB."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import TypeAlias

from engine.parser.span import Span


@dataclass(frozen=True, slots=True)
class Identifier:
    name: str
    span: Span


class SqlTypeName(StrEnum):
    INT = "INT"
    DOUBLE = "DOUBLE"
    VARCHAR = "VARCHAR"
    BOOL = "BOOL"
    DATE = "DATE"


@dataclass(frozen=True, slots=True)
class SqlType:
    name: SqlTypeName
    length: int | None
    span: Span


@dataclass(frozen=True, slots=True)
class ColumnDefinition:
    name: Identifier
    data_type: SqlType
    primary_key: bool
    span: Span


class StorageKind(StrEnum):
    HEAP = "HEAP"
    SEQUENTIAL = "SEQUENTIAL"


@dataclass(frozen=True, slots=True)
class IntegerLiteral:
    value: int
    span: Span


@dataclass(frozen=True, slots=True)
class DoubleLiteral:
    value: float
    span: Span


@dataclass(frozen=True, slots=True)
class StringLiteral:
    value: str
    span: Span


@dataclass(frozen=True, slots=True)
class BooleanLiteral:
    value: bool
    span: Span


@dataclass(frozen=True, slots=True)
class DateLiteral:
    value: date
    span: Span


Literal: TypeAlias = IntegerLiteral | DoubleLiteral | StringLiteral | BooleanLiteral | DateLiteral


@dataclass(frozen=True, slots=True)
class Wildcard:
    span: Span


@dataclass(frozen=True, slots=True)
class ColumnReference:
    """Columna referida en la consulta.

    ``qualifier`` es la tabla que la califica en ``tabla.columna``. Es ``None``
    en las referencias sin calificar, que son las unicas que existian antes de
    que el ``FROM`` admitiera mas de una fuente.
    """

    name: Identifier
    span: Span
    qualifier: Identifier | None = None


class AggregateFunction(StrEnum):
    COUNT = "COUNT"
    SUM = "SUM"
    MIN = "MIN"
    MAX = "MAX"
    AVG = "AVG"


@dataclass(frozen=True, slots=True)
class AggregateCall:
    function: AggregateFunction
    argument: ColumnReference | Wildcard
    span: Span


Projection: TypeAlias = Wildcard | ColumnReference | AggregateCall


class ComparisonOperator(StrEnum):
    EQUAL = "="
    LESS_THAN = "<"
    LESS_THAN_OR_EQUAL = "<="
    GREATER_THAN = ">"
    GREATER_THAN_OR_EQUAL = ">="


@dataclass(frozen=True, slots=True)
class ComparisonCondition:
    column: ColumnReference
    operator: ComparisonOperator
    value: Literal
    span: Span


@dataclass(frozen=True, slots=True)
class BetweenCondition:
    column: ColumnReference
    lower: Literal
    upper: Literal
    span: Span


Condition: TypeAlias = ComparisonCondition | BetweenCondition


class OrderDirection(StrEnum):
    ASC = "ASC"
    DESC = "DESC"


@dataclass(frozen=True, slots=True)
class OrderBy:
    column: ColumnReference
    direction: OrderDirection
    span: Span


@dataclass(frozen=True, slots=True)
class GroupBy:
    column: ColumnReference
    span: Span


@dataclass(frozen=True, slots=True)
class CreateTableStatement:
    table: Identifier
    columns: tuple[ColumnDefinition, ...]
    storage: StorageKind
    span: Span


@dataclass(frozen=True, slots=True)
class InsertStatement:
    table: Identifier
    values: tuple[Literal, ...]
    span: Span


@dataclass(frozen=True, slots=True)
class TableRef:
    """Hoja del arbol de ``FROM``: una tabla nombrada."""

    table: Identifier
    span: Span


@dataclass(frozen=True, slots=True)
class JoinRef:
    """Equijoin interno de dos fuentes por una columna de cada lado.

    Es recursivo a proposito: la gramatica de esta entrega acepta un solo
    ``JOIN``, pero admitir ``a JOIN b JOIN c`` mas adelante es ampliar el
    parser sin volver a mover el contrato que consumen la semantica y el
    planner.
    """

    left: FromSource
    right: FromSource
    left_column: ColumnReference
    right_column: ColumnReference
    span: Span


FromSource: TypeAlias = TableRef | JoinRef


@dataclass(frozen=True, slots=True)
class SelectStatement:
    projections: tuple[Projection, ...]
    source: FromSource
    where: Condition | None
    group_by: GroupBy | None
    order_by: OrderBy | None
    span: Span


@dataclass(frozen=True, slots=True)
class DeleteStatement:
    table: Identifier
    where: Condition
    span: Span


@dataclass(frozen=True, slots=True)
class BeginTransactionStatement:
    span: Span


@dataclass(frozen=True, slots=True)
class EndTransactionStatement:
    span: Span


Statement: TypeAlias = (
    CreateTableStatement
    | InsertStatement
    | SelectStatement
    | DeleteStatement
    | BeginTransactionStatement
    | EndTransactionStatement
)
