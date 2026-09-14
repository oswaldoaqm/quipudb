"""Nodos semanticos listos para cruzar la frontera con el core."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import TypeAlias

from engine.parser.ast import (
    AggregateFunction,
    ComparisonOperator,
    OrderDirection,
    SqlTypeName,
    StorageKind,
)
from engine.parser.span import Span

_FIXED_TYPE_SIZES = {
    SqlTypeName.INT: 4,
    SqlTypeName.DOUBLE: 8,
    SqlTypeName.BOOL: 1,
    SqlTypeName.DATE: 4,
}


@dataclass(frozen=True, slots=True)
class BoundColumn:
    """Columna validada con el mismo tamano fisico que ``quipudb::Column``."""

    name: str
    data_type: SqlTypeName
    length: int | None

    @property
    def byte_size(self) -> int:
        """Bytes fijos que ocupa la columna en un registro serializado."""

        if self.data_type is SqlTypeName.VARCHAR:
            if self.length is None:
                raise ValueError("VARCHAR necesita una longitud")
            return self.length
        return _FIXED_TYPE_SIZES[self.data_type]


@dataclass(frozen=True, slots=True)
class BoundSchema:
    """Esquema validado e independiente del modulo nativo opcional."""

    table_name: str
    columns: tuple[BoundColumn, ...]
    key_column: int

    @property
    def record_size(self) -> int:
        """Suma de los tamanos fijos, igual a ``quipudb::Schema::record_size``."""

        return sum(column.byte_size for column in self.columns)


@dataclass(frozen=True, slots=True)
class BoundCreateTable:
    """CREATE TABLE semanticamente valido."""

    schema: BoundSchema
    storage: StorageKind
    span: Span


BoundValue: TypeAlias = int | float | str | bool | date


@dataclass(frozen=True, slots=True)
class BoundInsertStatement:
    """INSERT con valores validados y convertidos a los tipos del esquema."""

    table_name: str
    values: tuple[BoundValue, ...]
    span: Span


@dataclass(frozen=True, slots=True)
class BoundColumnReference:
    """Columna resuelta a su posicion exacta dentro del esquema."""

    index: int
    column: BoundColumn
    span: Span


@dataclass(frozen=True, slots=True)
class BoundAggregateCall:
    """Agregado validado; ``argument=None`` representa exactamente ``COUNT(*)``."""

    function: AggregateFunction
    argument: BoundColumnReference | None
    span: Span


BoundProjection: TypeAlias = BoundColumnReference | BoundAggregateCall


@dataclass(frozen=True, slots=True)
class BoundGroupBy:
    """Unica clave de agrupacion resuelta contra el esquema de entrada."""

    column: BoundColumnReference
    span: Span


@dataclass(frozen=True, slots=True)
class BoundOrderBy:
    """Unica clave y direccion del ordenamiento resueltas semanticamente."""

    column: BoundColumnReference
    direction: OrderDirection
    span: Span


@dataclass(frozen=True, slots=True)
class BoundComparisonCondition:
    """Comparacion simple con su literal convertido al tipo de la columna."""

    column: BoundColumnReference
    operator: ComparisonOperator
    value: BoundValue
    span: Span


@dataclass(frozen=True, slots=True)
class BoundBetweenCondition:
    """Rango inclusivo con ambos limites semanticamente validados."""

    column: BoundColumnReference
    lower: BoundValue
    upper: BoundValue
    span: Span


BoundCondition: TypeAlias = BoundComparisonCondition | BoundBetweenCondition


@dataclass(frozen=True, slots=True)
class BoundSelectStatement:
    """SELECT con nombres resueltos y predicado listo para planificar."""

    schema: BoundSchema
    projections: tuple[BoundProjection, ...]
    wildcard: bool
    where: BoundCondition | None
    span: Span
    group_by: BoundGroupBy | None = None
    order_by: BoundOrderBy | None = None


@dataclass(frozen=True, slots=True)
class BoundDeleteStatement:
    """DELETE con tabla y predicado resueltos contra el esquema."""

    schema: BoundSchema
    where: BoundCondition
    span: Span


__all__ = [
    "BoundAggregateCall",
    "BoundBetweenCondition",
    "BoundColumn",
    "BoundColumnReference",
    "BoundComparisonCondition",
    "BoundCondition",
    "BoundCreateTable",
    "BoundDeleteStatement",
    "BoundGroupBy",
    "BoundInsertStatement",
    "BoundOrderBy",
    "BoundProjection",
    "BoundSchema",
    "BoundSelectStatement",
    "BoundValue",
]
