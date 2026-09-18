"""Seleccion de rutas fisicas para ``SELECT`` y ``DELETE``.

El optimizador trabaja solo con metadata copiada y con el arbol semantico. No
abre archivos ni construye ``Step``: esos pasos incluyen medidas reales y los
arma el ejecutor despues de recorrer la ruta elegida.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import TypeAlias

from engine.parser.ast import ComparisonOperator
from engine.parser.bound_ast import (
    BoundBetweenCondition,
    BoundComparisonCondition,
    BoundCondition,
    BoundDeleteStatement,
    BoundJoinRef,
    BoundSelectStatement,
    BoundSource,
    BoundTableRef,
)
from engine.planner.plan import Structure


class AccessRoute(StrEnum):
    """Operacion nativa que inicia la lectura de las filas candidatas."""

    SCAN = "scan"
    TABLE_SEARCH = "table_search"
    TABLE_RANGE = "table_range"
    INDEX_SEARCH = "index_search"
    INDEX_RANGE = "index_range"


class JoinStrategy(StrEnum):
    """Estrategia que se le PIDE a ``ExternalJoin``.

    No aparece ``INDEX_NESTED``: el planner nunca lo impone. Si hay una sonda
    aplicable pide ``AUTO`` y deja que la regla medida elija entre los dos
    caminos con la aritmetica de paginas exacta, que vive en el core; si no la
    hay, pide ``HASH``, que es el unico camino posible.
    """

    AUTO = "auto"
    HASH = "hash"


class GroupStrategy(StrEnum):
    """Estrategia solicitada al agrupador externo del core."""

    AUTO = "auto"
    HASH = "hash"


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
    rows: int | None = None
    """Filas de la tabla, de ``TableFile::size()``.

    ``None`` significa "no se sabe". El ejecutor lo traduce a ``left_rows=0``,
    que en ``ExternalJoin`` elige hash join: equivocarse hacia hash cuesta un
    factor dos y equivocarse hacia index nested loop cuesta hasta 2180x.
    """

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("la tabla necesita un nombre")
        if self.rows is not None and self.rows < 0:
            raise ValueError("el conteo de filas no puede ser negativo")
        if self.structure not in {
            Structure.HEAP,
            Structure.SEQUENTIAL,
            Structure.BPLUS_CLUSTERED,
        }:
            raise ValueError(f"estructura de tabla invalida: {self.structure.value}")
        object.__setattr__(self, "indexes", tuple(self.indexes))


@dataclass(frozen=True, slots=True)
class PhysicalTableAccess:
    """Hoja fisica: como se leen las filas candidatas de UNA tabla."""

    table: TableMetadata
    route: AccessRoute
    index: IndexMetadata | None = None
    residual_filter: bool = False

    def __post_init__(self) -> None:
        index_route = self.route in {AccessRoute.INDEX_SEARCH, AccessRoute.INDEX_RANGE}
        if index_route != (self.index is not None):
            raise ValueError("las rutas de indice necesitan exactamente un indice")
        if self.index is None:
            return
        if self.index not in self.table.indexes:
            raise ValueError("el indice elegido no pertenece a la metadata de la tabla")
        if self.route is AccessRoute.INDEX_RANGE and not self.index.supports_range:
            raise ValueError("INDEX_RANGE necesita un indice que soporte rangos")


@dataclass(frozen=True, slots=True)
class PhysicalJoin:
    """Nodo de join. La aritmetica de paginas NO se hace aqui.

    El planner decide lo unico que puede decidir sin duplicar la formula del
    core: si hay una sonda aplicable sobre la columna de join y, por tanto, si
    pide ``AUTO`` o ``HASH``. Entre index nested loop y hash join elige
    ``ExternalJoin`` con la regla medida, que es donde el tamano de pagina y la
    cabecera son exactos.
    """

    left: PhysicalSource
    right: PhysicalSource
    left_column: int
    right_column: int
    strategy: JoinStrategy
    probe: IndexMetadata | None = None
    probe_on_primary_key: bool = False
    residual_filter: bool = False

    def __post_init__(self) -> None:
        tiene_sonda = self.probe is not None or self.probe_on_primary_key
        if self.strategy is JoinStrategy.AUTO and not tiene_sonda:
            raise ValueError("AUTO solo tiene sentido si hay una sonda que evaluar")
        if self.strategy is JoinStrategy.HASH and tiene_sonda:
            raise ValueError("con una sonda disponible la eleccion la hace AUTO")
        if self.probe is not None and self.probe_on_primary_key:
            raise ValueError("la sonda es el indice secundario o la clave primaria, no ambos")

    @property
    def left_rows(self) -> int | None:
        """Filas del lado externo, que es lo que ``AUTO`` necesita para decidir.

        Solo se conoce cuando el lado izquierdo es una tabla: la salida de un
        join anidado no tiene conteo sin ejecutarlo.
        """

        if isinstance(self.left, PhysicalTableAccess):
            return self.left.table.rows
        return None


PhysicalSource: TypeAlias = PhysicalTableAccess | PhysicalJoin


@dataclass(frozen=True, slots=True)
class PhysicalSelectPlan:
    """IR fisico previo a ejecutar y medir los ``Step`` del ADR 0002."""

    statement: BoundSelectStatement
    source: PhysicalSource
    external_sort: bool = False
    group_strategy: GroupStrategy | None = None

    def __post_init__(self) -> None:
        if self.external_sort != (self.statement.order_by is not None):
            raise ValueError("ORDER BY y external_sort deben aparecer juntos")
        if (self.group_strategy is not None) != (self.statement.group_by is not None):
            raise ValueError("GROUP BY necesita exactamente una estrategia de agrupacion")
        _validate_source(self.source, self.statement.source, self.statement.where)

    @property
    def access(self) -> PhysicalTableAccess:
        """La hoja unica de un plan sobre una sola tabla.

        El ejecutor todavia asume una tabla; recorrer el arbol es el siguiente
        paso. Pedir esto sobre un join falla en vez de mentir.
        """

        if not isinstance(self.source, PhysicalTableAccess):
            raise TypeError("este plan es un join: recorre source en vez de pedir una hoja")
        return self.source

    @property
    def table(self) -> TableMetadata:
        return self.access.table

    @property
    def route(self) -> AccessRoute:
        return self.access.route

    @property
    def index(self) -> IndexMetadata | None:
        return self.access.index

    @property
    def residual_filter(self) -> bool:
        """Si queda predicado por evaluar en memoria por encima de la fuente."""

        return self.source.residual_filter


@dataclass(frozen=True, slots=True)
class PhysicalDeletePlan:
    """IR fisico para capturar las filas que se borraran antes de mutar."""

    statement: BoundDeleteStatement
    table: TableMetadata
    route: AccessRoute
    index: IndexMetadata | None = None
    residual_filter: bool = False

    def __post_init__(self) -> None:
        _validate_access(
            self.statement.schema.table_name,
            self.statement.where,
            self.table,
            self.route,
            self.index,
        )
        if self.table.indexes and self.route in {
            AccessRoute.TABLE_SEARCH,
            AccessRoute.TABLE_RANGE,
        }:
            raise ValueError("DELETE con indices necesita una ruta que conserve el RID")


@dataclass(frozen=True, slots=True)
class _AccessChoice:
    route: AccessRoute
    index: IndexMetadata | None = None
    residual_filter: bool = False


def optimize_select(
    statement: BoundSelectStatement,
    tables: TableMetadata | Mapping[str, TableMetadata],
) -> PhysicalSelectPlan:
    """Elige una ruta determinista para un ``SELECT`` semanticamente valido.

    La clave primaria tiene prioridad porque ``TableFile`` puede resolverla
    sin el paso adicional de ``fetch``. Para igualdad se prefiere hash sobre
    B+; para rango se descarta hash. Entre indices equivalentes se usa el
    nombre como desempate estable, no el orden en que llegaron del catalogo.

    Con un join, el predicado baja a la hoja cuando todas sus columnas son de
    esa hoja -- y entonces ``_choose_access`` decide alli exactamente igual que
    siempre. Si no, se evalua en memoria por encima del join. Empujar
    predicados a los dos lados a la vez queda fuera de este alcance.

    ``tables`` admite una sola ``TableMetadata`` -- la forma de siempre -- o un
    mapa de nombre a metadata, que es lo que un join necesita.
    """

    catalogo: Mapping[str, TableMetadata] = (
        {tables.name: tables} if isinstance(tables, TableMetadata) else tables
    )
    dueno = _leaf_of(statement.source, statement.where)
    source = _optimize_source(
        statement.source,
        catalogo,
        statement.where,
        dueno,
        offset=0,
        primary_table_allowed=True,
    )
    return PhysicalSelectPlan(
        statement=statement,
        source=source,
        external_sort=statement.order_by is not None,
        group_strategy=(GroupStrategy.AUTO if statement.group_by is not None else None),
    )


def _leaf_of(source: BoundSource, where: BoundCondition | None) -> BoundTableRef | None:
    """Hoja a la que pertenece la columna del predicado, si es de una sola."""

    if where is None:
        return None
    objetivo = where.column.index
    for hoja, inicio in _leaves(source):
        if inicio <= objetivo < inicio + len(hoja.schema.columns):
            return hoja
    return None


def _leaves(source: BoundSource, offset: int = 0) -> list[tuple[BoundTableRef, int]]:
    if isinstance(source, BoundTableRef):
        return [(source, offset)]
    return _leaves(source.left, offset) + _leaves(
        source.right, offset + len(source.left.schema.columns)
    )


def _optimize_source(
    node: BoundSource,
    catalogo: Mapping[str, TableMetadata],
    where: BoundCondition | None,
    dueno: BoundTableRef | None,
    offset: int,
    *,
    primary_table_allowed: bool,
) -> PhysicalSource:
    if isinstance(node, BoundTableRef):
        table = catalogo.get(node.schema.table_name)
        if table is None:
            raise ValueError(
                f"falta la metadata de la tabla {node.schema.table_name!r} que usa la consulta"
            )
        if node is not dueno:
            # El predicado no es de esta hoja: se lee entera y se filtra arriba.
            return PhysicalTableAccess(table, AccessRoute.SCAN)
        assert where is not None
        choice = _choose_access(
            where,
            where.column.index - offset,
            node.schema.key_column,
            table,
            primary_table_allowed=primary_table_allowed,
        )
        return PhysicalTableAccess(table, choice.route, choice.index, choice.residual_filter)

    left = _optimize_source(
        node.left, catalogo, where, dueno, offset, primary_table_allowed=primary_table_allowed
    )
    right = _optimize_source(
        node.right,
        catalogo,
        where,
        dueno,
        offset + len(node.left.schema.columns),
        primary_table_allowed=primary_table_allowed,
    )

    probe, on_primary = _choose_probe(node, right)
    return PhysicalJoin(
        left=left,
        right=right,
        left_column=node.left_column.index,
        right_column=node.right_column.index,
        strategy=(
            JoinStrategy.AUTO if (probe is not None or on_primary) else JoinStrategy.HASH
        ),
        probe=probe,
        probe_on_primary_key=on_primary,
        residual_filter=where is not None and dueno is None,
    )


def _choose_probe(
    node: BoundJoinRef,
    right: PhysicalSource,
) -> tuple[IndexMetadata | None, bool]:
    """Con que se podria sondear el lado derecho por la columna de join.

    Solo una hoja se puede sondear: la salida de otro join no se busca por
    clave, se recorre. La clave primaria gana al indice secundario porque
    ``TableFile::search`` resuelve sin el ``fetch`` adicional.
    """

    if not isinstance(right, PhysicalTableAccess):
        return None, False
    columna = node.right_column.index
    if columna == node.right.schema.key_column and right.table.structure in {
        Structure.SEQUENTIAL,
        Structure.BPLUS_CLUSTERED,
    }:
        return None, True
    return _best_index(right.table, columna, for_range=False), False


def _validate_source(
    source: PhysicalSource,
    bound: BoundSource,
    where: BoundCondition | None,
) -> None:
    fisicas = _physical_leaves(source)
    semanticas = _leaves(bound)
    if [hoja.table.name for hoja in fisicas] != [
        hoja.schema.table_name for hoja, _ in semanticas
    ]:
        raise ValueError(
            f"la metadata describe {[h.table.name for h in fisicas]} y la sentencia "
            f"consulta {[h.schema.table_name for h, _ in semanticas]}"
        )

    dueno = _leaf_of(bound, where)
    for fisica, (semantica, offset) in zip(fisicas, semanticas, strict=True):
        if semantica is not dueno:
            if fisica.index is not None or fisica.route is not AccessRoute.SCAN:
                raise ValueError(
                    f"la hoja {fisica.table.name!r} no tiene el predicado: solo puede recorrerse"
                )
            continue
        assert where is not None
        columna = where.column.index - offset
        if fisica.index is not None and fisica.index.column != columna:
            raise ValueError("el indice elegido no corresponde a la columna del predicado")
        if (
            fisica.route in {AccessRoute.TABLE_SEARCH, AccessRoute.TABLE_RANGE}
            and columna != semantica.schema.key_column
        ):
            raise ValueError("una ruta de tabla necesita la columna clave del predicado")


def _physical_leaves(source: PhysicalSource) -> list[PhysicalTableAccess]:
    if isinstance(source, PhysicalTableAccess):
        return [source]
    return _physical_leaves(source.left) + _physical_leaves(source.right)


def optimize_delete(
    statement: BoundDeleteStatement,
    table: TableMetadata,
) -> PhysicalDeletePlan:
    """Elige candidatos sin perder los RID que necesitan los indices.

    Las tablas con indices secundarios son heap files. Si el predicado no
    tiene un indice aplicable, DELETE hace scan con RID y filtra; una busqueda
    de tabla devolveria solo registros y no permitiria retirar cada par
    secundario exacto.
    """

    choice = _choose_access(
        statement.where,
        statement.where.column.index,
        statement.schema.key_column,
        table,
        primary_table_allowed=not table.indexes,
    )
    return PhysicalDeletePlan(
        statement,
        table,
        choice.route,
        choice.index,
        choice.residual_filter,
    )


def _choose_access(
    where: BoundCondition | None,
    column: int,
    key_column: int,
    table: TableMetadata,
    *,
    primary_table_allowed: bool,
) -> _AccessChoice:
    """``column`` es la posicion DENTRO de la hoja, no en el esquema de salida.

    Con una sola tabla las dos coinciden; con un join hay que restar el
    desplazamiento del lado al que pertenece la columna.
    """

    if where is None:
        return _AccessChoice(AccessRoute.SCAN)

    is_primary_key = column == key_column
    if isinstance(where, BoundBetweenCondition):
        return _plan_range(
            table,
            column,
            is_primary_key and primary_table_allowed,
            strict=False,
        )

    if not isinstance(where, BoundComparisonCondition):
        raise TypeError(f"condicion enlazada desconocida: {type(where).__name__}")

    if where.operator is ComparisonOperator.EQUAL:
        return _plan_equality(
            where,
            table,
            column,
            is_primary_key and primary_table_allowed,
        )

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
        return _plan_range(
            table,
            column,
            is_primary_key and primary_table_allowed,
            strict,
        )

    raise ValueError(f"operador de comparacion desconocido: {where.operator!r}")


def _plan_equality(
    condition: BoundComparisonCondition,
    table: TableMetadata,
    column: int,
    use_primary_table: bool,
) -> _AccessChoice:
    if use_primary_table:
        return _AccessChoice(AccessRoute.TABLE_SEARCH)

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
        return _AccessChoice(
            AccessRoute.INDEX_SEARCH,
            index=index,
        )
    return _AccessChoice(
        AccessRoute.SCAN,
        residual_filter=True,
    )


def _plan_range(
    table: TableMetadata,
    column: int,
    use_primary_table: bool,
    strict: bool,
) -> _AccessChoice:
    if use_primary_table:
        return _AccessChoice(
            AccessRoute.TABLE_RANGE,
            residual_filter=strict,
        )

    index = _best_index(table, column, for_range=True)
    if index is not None:
        return _AccessChoice(
            AccessRoute.INDEX_RANGE,
            index=index,
            residual_filter=strict,
        )
    return _AccessChoice(
        AccessRoute.SCAN,
        residual_filter=True,
    )


def _validate_access(
    table_name: str,
    where: BoundCondition | None,
    table: TableMetadata,
    route: AccessRoute,
    index: IndexMetadata | None,
) -> None:
    index_route = route in {AccessRoute.INDEX_SEARCH, AccessRoute.INDEX_RANGE}
    if index_route != (index is not None):
        raise ValueError("las rutas de indice necesitan exactamente un indice")
    if table.name != table_name:
        raise ValueError("la metadata y la sentencia pertenecen a tablas distintas")
    if index is None:
        return
    if index not in table.indexes:
        raise ValueError("el indice elegido no pertenece a la metadata de la tabla")
    if where is None:
        raise ValueError("una ruta de indice necesita una condicion")
    if index.column != where.column.index:
        raise ValueError("el indice elegido no corresponde a la columna del predicado")
    if route is AccessRoute.INDEX_RANGE and not index.supports_range:
        raise ValueError("INDEX_RANGE necesita un indice que soporte rangos")


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
    "GroupStrategy",
    "IndexMetadata",
    "JoinStrategy",
    "PhysicalDeletePlan",
    "PhysicalJoin",
    "PhysicalSelectPlan",
    "PhysicalSource",
    "PhysicalTableAccess",
    "TableMetadata",
    "optimize_delete",
    "optimize_select",
]
