"""Seleccion de rutas fisicas para ``SELECT`` del issue #26.

El optimizador trabaja solo con metadata copiada y con el arbol semantico. No
abre archivos ni construye ``Step``: esos pasos incluyen medidas reales y los
arma el ejecutor despues de recorrer la ruta elegida.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from engine.parser.ast import ComparisonOperator
from engine.parser.bound_ast import (
    BoundBetweenCondition,
    BoundComparisonCondition,
    BoundSelectStatement,
)
from engine.planner.plan import Structure


class AccessRoute(StrEnum):
    """Operacion nativa que inicia la lectura de las filas candidatas."""

    SCAN = "scan"
    TABLE_SEARCH = "table_search"
    TABLE_RANGE = "table_range"
    INDEX_SEARCH = "index_search"
    INDEX_RANGE = "index_range"


@dataclass(frozen=True, slots=True)
class IndexMetadata:
    """Descripcion inmutable de un indice secundario disponible."""

    name: str
    column: int
    structure: Structure
    supports_range: bool

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("el indice necesita un nombre")
        if self.column < 0:
            raise ValueError("la posicion de la columna del indice no puede ser negativa")
        if self.structure not in {
            Structure.BPLUS_UNCLUSTERED,
            Structure.EXTENDIBLE_HASH,
        }:
            raise ValueError(f"estructura de indice secundario invalida: {self.structure.value}")
        expected_range = self.structure is Structure.BPLUS_UNCLUSTERED
        if self.supports_range is not expected_range:
            raise ValueError(
                f"supports_range={self.supports_range!r} contradice {self.structure.value}"
            )


@dataclass(frozen=True, slots=True)
class TableMetadata:
    """Metadata del catalogo necesaria para decidir, sin handles nativos."""

    name: str
    structure: Structure
    indexes: tuple[IndexMetadata, ...] = ()

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("la tabla necesita un nombre")
        if self.structure not in {
            Structure.HEAP,
            Structure.SEQUENTIAL,
            Structure.BPLUS_CLUSTERED,
        }:
            raise ValueError(f"estructura de tabla invalida: {self.structure.value}")
        object.__setattr__(self, "indexes", tuple(self.indexes))


@dataclass(frozen=True, slots=True)
class PhysicalSelectPlan:
    """IR fisico previo a ejecutar y medir los ``Step`` del ADR 0002."""

    statement: BoundSelectStatement
    table: TableMetadata
    route: AccessRoute
    index: IndexMetadata | None = None
    residual_filter: bool = False

    def __post_init__(self) -> None:
        index_route = self.route in {AccessRoute.INDEX_SEARCH, AccessRoute.INDEX_RANGE}
        if index_route != (self.index is not None):
            raise ValueError("las rutas de indice necesitan exactamente un indice")
        if self.table.name != self.statement.schema.table_name:
            raise ValueError("la metadata y el SELECT pertenecen a tablas distintas")
        if self.index is None:
            return
        if self.index not in self.table.indexes:
            raise ValueError("el indice elegido no pertenece a la metadata de la tabla")
        if self.statement.where is None:
            raise ValueError("una ruta de indice necesita una condicion")
        if self.index.column != self.statement.where.column.index:
            raise ValueError("el indice elegido no corresponde a la columna del predicado")
        if self.route is AccessRoute.INDEX_RANGE and not self.index.supports_range:
            raise ValueError("INDEX_RANGE necesita un indice que soporte rangos")


def optimize_select(
    statement: BoundSelectStatement,
    table: TableMetadata,
) -> PhysicalSelectPlan:
    """Elige una ruta determinista para un ``SELECT`` semanticamente valido.

    La clave primaria tiene prioridad porque ``TableFile`` puede resolverla
    sin el paso adicional de ``fetch``. Para igualdad se prefiere hash sobre
    B+; para rango se descarta hash. Entre indices equivalentes se usa el
    nombre como desempate estable, no el orden en que llegaron del catalogo.
    """

    where = statement.where
    if where is None:
        return PhysicalSelectPlan(statement, table, AccessRoute.SCAN)

    column = where.column.index
    is_primary_key = column == statement.schema.key_column
    if isinstance(where, BoundBetweenCondition):
        return _plan_range(statement, table, column, is_primary_key, strict=False)

    if not isinstance(where, BoundComparisonCondition):
        raise TypeError(f"condicion enlazada desconocida: {type(where).__name__}")

    if where.operator is ComparisonOperator.EQUAL:
        return _plan_equality(statement, table, column, is_primary_key)

    if where.operator in {
        ComparisonOperator.LESS_THAN,
        ComparisonOperator.LESS_THAN_OR_EQUAL,
        ComparisonOperator.GREATER_THAN,
        ComparisonOperator.GREATER_THAN_OR_EQUAL,
    }:
        strict = where.operator in {
            ComparisonOperator.LESS_THAN,
            ComparisonOperator.GREATER_THAN,
        }
        return _plan_range(statement, table, column, is_primary_key, strict)

    raise ValueError(f"operador de comparacion desconocido: {where.operator!r}")


def _plan_equality(
    statement: BoundSelectStatement,
    table: TableMetadata,
    column: int,
    is_primary_key: bool,
) -> PhysicalSelectPlan:
    if is_primary_key:
        return PhysicalSelectPlan(statement, table, AccessRoute.TABLE_SEARCH)

    condition = statement.where
    allow_hash = not (
        isinstance(condition.value, str)
        and (
            "\0" in condition.value
            or (
                condition.column.column.length is not None
                and len(condition.value.encode("utf-8")) > condition.column.column.length
            )
        )
    )
    index = _best_index(table, column, for_range=False, allow_hash=allow_hash)
    if index is not None:
        return PhysicalSelectPlan(
            statement,
            table,
            AccessRoute.INDEX_SEARCH,
            index=index,
        )
    return PhysicalSelectPlan(
        statement,
        table,
        AccessRoute.SCAN,
        residual_filter=True,
    )


def _plan_range(
    statement: BoundSelectStatement,
    table: TableMetadata,
    column: int,
    is_primary_key: bool,
    strict: bool,
) -> PhysicalSelectPlan:
    if is_primary_key:
        return PhysicalSelectPlan(
            statement,
            table,
            AccessRoute.TABLE_RANGE,
            residual_filter=strict,
        )

    index = _best_index(table, column, for_range=True)
    if index is not None:
        return PhysicalSelectPlan(
            statement,
            table,
            AccessRoute.INDEX_RANGE,
            index=index,
            residual_filter=strict,
        )
    return PhysicalSelectPlan(
        statement,
        table,
        AccessRoute.SCAN,
        residual_filter=True,
    )


def _best_index(
    table: TableMetadata,
    column: int,
    *,
    for_range: bool,
    allow_hash: bool = True,
) -> IndexMetadata | None:
    candidates = [index for index in table.indexes if index.column == column]
    if for_range:
        candidates = [index for index in candidates if index.supports_range]
        ranking = {Structure.BPLUS_UNCLUSTERED: 0}
    else:
        if not allow_hash:
            candidates = [
                index for index in candidates if index.structure is not Structure.EXTENDIBLE_HASH
            ]
        ranking = {
            Structure.EXTENDIBLE_HASH: 0,
            Structure.BPLUS_UNCLUSTERED: 1,
        }
    return min(candidates, key=lambda index: (ranking[index.structure], index.name), default=None)


__all__ = [
    "AccessRoute",
    "IndexMetadata",
    "PhysicalSelectPlan",
    "TableMetadata",
    "optimize_select",
]
