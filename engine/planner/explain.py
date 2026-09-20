"""Traduccion de un plan fisico no ejecutado al contrato publico ``Plan``."""

from __future__ import annotations

from engine.parser.ast import AggregateFunction
from engine.parser.bound_ast import (
    BoundAggregateCall,
    BoundColumnReference,
    BoundCondition,
    BoundJoinRef,
    BoundSelectStatement,
    BoundSource,
    BoundTableRef,
)
from engine.planner.optimizer import (
    AccessRoute,
    GroupStrategy,
    JoinStrategy,
    PhysicalJoin,
    PhysicalSelectPlan,
    PhysicalSource,
    PhysicalTableAccess,
)
from engine.planner.plan import Op, Plan, Step, Structure


def explain_select(
    physical: PhysicalSelectPlan,
    query: str,
    source: str,
    *,
    planning_ms: float = 0.0,
) -> Plan:
    """Describe la ruta elegida sin ejecutar operadores ni leer filas.

    Los contadores y tiempos de cada ``Step`` quedan en cero a proposito. Solo
    ``EXPLAIN ANALYZE`` ejecuta operadores y reemplaza esta descripcion por el
    arbol instrumentado que ya construye el ejecutor.
    """

    statement = physical.statement
    root = _source_step(
        physical.source,
        statement.source,
        statement.where,
        source,
    )

    if statement.group_by is not None:
        strategy = physical.group_strategy
        if strategy is None:  # protegido por PhysicalSelectPlan
            raise ValueError("GROUP BY necesita una estrategia fisica")
        detail = f"estrategia solicitada {strategy.value.upper()}"
        if strategy is GroupStrategy.AUTO:
            detail += "; hash con posible fallback a sort al ejecutar"
        root = Step(
            op=Op.GROUP,
            structure=Structure.EXTERNAL_HASH,
            table=statement.schema.table_name,
            column=statement.group_by.column.column.name,
            detail=detail,
            children=[root],
        )

    if statement.order_by is not None:
        detail = f"direccion {statement.order_by.direction.value}; external sort"
        if statement.group_by is not None:
            detail += "; puede reutilizar el orden producido por GROUP BY"
        root = Step(
            op=Op.SORT,
            structure=Structure.EXTERNAL_SORT,
            table=statement.schema.table_name,
            column=statement.order_by.column.column.name,
            detail=detail,
            children=[root],
        )

    if statement.group_by is not None or not statement.wildcard:
        root = Step(
            op=Op.PROJECT,
            structure=Structure.MEMORY,
            table=statement.schema.table_name,
            detail=", ".join(_projection_names(statement)),
            children=[root],
        )

    return Plan(query=query, root=root, time_ms=planning_ms)


def _source_step(
    physical: PhysicalSource,
    semantic: BoundSource,
    condition: BoundCondition | None,
    source: str,
) -> Step:
    if isinstance(physical, PhysicalTableAccess):
        if not isinstance(semantic, BoundTableRef):
            raise TypeError("un acceso de tabla necesita una fuente semantica de tabla")
        root = _access_step(physical, semantic, condition, source)
    else:
        if not isinstance(semantic, BoundJoinRef):
            raise TypeError("un join fisico necesita un join semantico")
        root = Step(
            op=Op.JOIN,
            structure=_join_structure(physical),
            table=semantic.schema.table_name,
            column=semantic.left_column.column.name,
            detail=_join_detail(physical),
            children=[
                _source_step(physical.left, semantic.left, condition, source),
                _source_step(physical.right, semantic.right, condition, source),
            ],
        )

    if not physical.residual_filter:
        return root
    if condition is None:  # protegido por PhysicalSelectPlan
        raise ValueError("un filtro residual necesita una condicion")
    return Step(
        op=Op.FILTER,
        structure=Structure.MEMORY,
        table=semantic.schema.table_name,
        column=condition.column.column.name,
        detail=_condition_text(condition, source),
        children=[root],
    )


def _access_step(
    access: PhysicalTableAccess,
    semantic: BoundTableRef,
    condition: BoundCondition | None,
    source: str,
) -> Step:
    table_name = semantic.schema.table_name
    if access.route is AccessRoute.SCAN:
        return Step(op=Op.SCAN, structure=access.table.structure, table=table_name)

    if condition is None:  # protegido por PhysicalSelectPlan
        raise ValueError(f"la ruta {access.route.value} necesita una condicion")
    common = {
        "table": table_name,
        "column": condition.column.column.name,
        "detail": _condition_text(condition, source),
    }
    if access.route is AccessRoute.TABLE_SEARCH:
        return Step(op=Op.SEARCH, structure=access.table.structure, **common)
    if access.route is AccessRoute.TABLE_RANGE:
        return Step(op=Op.RANGE_SEARCH, structure=access.table.structure, **common)

    index = access.index
    if index is None:  # protegido por PhysicalTableAccess
        raise ValueError(f"la ruta {access.route.value} necesita un indice")
    operation = Op.INDEX_SEARCH if access.route is AccessRoute.INDEX_SEARCH else Op.INDEX_RANGE
    index_step = Step(
        op=operation,
        structure=index.structure,
        detail=f"indice {index.name}; {common['detail']}",
        table=table_name,
        column=condition.column.column.name,
    )
    return Step(
        op=Op.FETCH,
        structure=access.table.structure,
        table=table_name,
        detail="lee los registros que indiquen los RID del indice",
        children=[index_step],
    )


def _join_structure(join: PhysicalJoin) -> Structure:
    if join.strategy is JoinStrategy.HASH:
        return Structure.EXTERNAL_HASH
    if join.probe is not None:
        return join.probe.structure
    if isinstance(join.right, PhysicalTableAccess):
        return join.right.table.structure
    raise ValueError("un join AUTO necesita una sonda en su lado derecho")


def _join_detail(join: PhysicalJoin) -> str:
    if join.strategy is JoinStrategy.HASH:
        return "estrategia HASH; external hash join"
    if join.probe is not None:
        probe = f"indice {join.probe.name}"
    else:
        probe = "clave primaria de la tabla derecha"
    return f"estrategia AUTO con sonda por {probe}; decide al ejecutar entre indice y hash"


def _projection_names(statement: BoundSelectStatement) -> list[str]:
    names: list[str] = []
    for projection in statement.projections:
        if isinstance(projection, BoundColumnReference):
            names.append(projection.column.name)
            continue
        names.append(_aggregate_name(projection))
    return names


def _aggregate_name(aggregate: BoundAggregateCall) -> str:
    suffix = "all" if aggregate.argument is None else aggregate.argument.column.name
    return f"{AggregateFunction(aggregate.function).value}_{suffix}"


def _condition_text(condition: BoundCondition, source: str) -> str:
    return source[condition.span.start : condition.span.end]


__all__ = ["explain_select"]
