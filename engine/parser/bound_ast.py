"""Nodos semanticos listos para cruzar la frontera con el core."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from typing import TypeAlias

from engine.parser.ast import (
    AggregateFunction,
    ComparisonOperator,
    IndexKind,
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


@dataclass(frozen=True, slots=True)
class BoundCreateIndexStatement:
    """CREATE INDEX validado contra el esquema de su tabla."""

    index_name: str
    table_name: str
    column_name: str
    kind: IndexKind
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
class BoundTableRef:
    """Hoja resuelta del ``FROM``: el esquema de una tabla del catalogo."""

    schema: BoundSchema
    span: Span


@dataclass(frozen=True, slots=True)
class BoundJoinRef:
    """Join resuelto. ``schema`` es la concatenacion de los dos lados.

    Las dos columnas de la condicion estan indexadas contra el esquema de SU
    lado, no contra la concatenacion: son lo que ``ExternalJoin`` recibe como
    ``left_column`` y ``right_column``.
    """

    left: BoundSource
    right: BoundSource
    left_column: BoundColumnReference
    right_column: BoundColumnReference
    schema: BoundSchema
    span: Span


BoundSource: TypeAlias = BoundTableRef | BoundJoinRef


def join_output_schema(left: BoundSchema, right: BoundSchema) -> BoundSchema:
    """Concatena dos esquemas con la regla de ``ExternalJoin::output_schema()``.

    Es deliberadamente la misma que el core: columnas de la izquierda seguidas
    de las de la derecha, y solo las que se llaman igual en los dos lados se
    prefijan con el nombre de su tabla. Prefijarlas todas ensuciaria el caso
    comun, que es lo que el Panel de Resultados muestra como cabecera. La
    columna de join sale dos veces, una por lado.

    ``key_column`` queda en 0 porque un join no tiene clave primaria y
    ``BoundSchema`` no sabe expresar que no la hay; nadie debe usarla.
    """

    colisionan = {column.name for column in left.columns} & {
        column.name for column in right.columns
    }

    def _prefijar(schema: BoundSchema) -> list[BoundColumn]:
        return [
            replace(column, name=f"{schema.table_name}.{column.name}")
            if column.name in colisionan
            else column
            for column in schema.columns
        ]

    return BoundSchema(
        f"{left.table_name}_{right.table_name}",
        tuple(_prefijar(left) + _prefijar(right)),
        0,
    )


@dataclass(frozen=True, slots=True)
class BoundSelectStatement:
    """SELECT con nombres resueltos y predicado listo para planificar."""

    source: BoundSource
    projections: tuple[BoundProjection, ...]
    wildcard: bool
    where: BoundCondition | None
    span: Span
    group_by: BoundGroupBy | None = None
    order_by: BoundOrderBy | None = None

    @property
    def schema(self) -> BoundSchema:
        """Esquema de salida de la fuente.

        Para una tabla sola es el esquema de la tabla, que es lo que este
        campo significaba antes de que el ``FROM`` admitiera un join. Por eso
        los indices de proyecciones, predicado, GROUP BY y ORDER BY siguen
        siendo posiciones contra ``statement.schema`` sin cambiar nada.
        """

        return self.source.schema


@dataclass(frozen=True, slots=True)
class BoundDeleteStatement:
    """DELETE con tabla y predicado resueltos contra el esquema."""

    schema: BoundSchema
    where: BoundCondition
    span: Span


@dataclass(frozen=True, slots=True)
class BoundDropTableStatement:
    """DROP TABLE con un identificador validado."""

    table_name: str
    span: Span


__all__ = [
    "BoundAggregateCall",
    "BoundBetweenCondition",
    "BoundColumn",
    "BoundColumnReference",
    "BoundComparisonCondition",
    "BoundCondition",
    "BoundCreateIndexStatement",
    "BoundCreateTable",
    "BoundDeleteStatement",
    "BoundDropTableStatement",
    "BoundGroupBy",
    "BoundInsertStatement",
    "BoundJoinRef",
    "BoundOrderBy",
    "BoundProjection",
    "BoundSchema",
    "BoundSelectStatement",
    "BoundSource",
    "BoundTableRef",
    "BoundValue",
    "join_output_schema",
]
