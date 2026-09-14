"""Operadores fisicos de ``SELECT`` y construccion de su plan medido."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from engine.executor.instrumentation import measure_memory, measure_native
from engine.executor.native import (
    from_native_record,
)
from engine.executor.predicates import equality_key, matches, range_values
from engine.parser.bound_ast import (
    BoundCondition,
    BoundValue,
)
from engine.planner.optimizer import AccessRoute, PhysicalSelectPlan
from engine.planner.plan import Op, Step, Structure


@dataclass(frozen=True, slots=True)
class SelectExecution:
    """Salida materializada de SELECT antes de envolverla en ``Plan``."""

    columns: tuple[str, ...]
    rows: tuple[tuple[BoundValue, ...], ...]
    root: Step


def execute_select(
    database: Any,
    native: Any,
    plan: PhysicalSelectPlan,
    source: str,
) -> SelectExecution:
    """Ejecuta una ruta fisica y arma el arbol ``Step`` con medidas reales."""

    _validate_plan(plan)
    statement = plan.statement
    table_name = statement.schema.table_name
    table = database.table(table_name)

    native_rows, root = _read_candidates(database, native, table, plan, source)
    rows = tuple(from_native_record(row, statement.schema) for row in native_rows)

    if plan.residual_filter:
        condition = statement.where
        if condition is None:  # protegido tambien por _validate_plan
            raise ValueError("un filtro residual necesita una condicion")
        filtered = measure_memory(
            lambda: tuple(row for row in rows if matches(row, condition)),
            records_examined=len(rows),
            records_returned=len,
        )
        rows = filtered.value
        root = Step(
            op=Op.FILTER,
            structure=Structure.MEMORY,
            table=table_name,
            column=condition.column.column.name,
            detail=_condition_text(condition, source),
            stats=filtered.stats,
            time_ms=filtered.time_ms,
            children=[root],
        )

    columns = tuple(projection.column.name for projection in statement.projections)
    if not statement.wildcard:
        projected = measure_memory(
            lambda: tuple(
                tuple(row[projection.index] for projection in statement.projections) for row in rows
            ),
            records_examined=len(rows),
            records_returned=len,
        )
        rows = projected.value
        root = Step(
            op=Op.PROJECT,
            structure=Structure.MEMORY,
            table=table_name,
            detail=", ".join(columns),
            stats=projected.stats,
            time_ms=projected.time_ms,
            children=[root],
        )

    return SelectExecution(columns, rows, root)


def _read_candidates(
    database: Any,
    native: Any,
    table: Any,
    plan: PhysicalSelectPlan,
    source: str,
) -> tuple[Any, Step]:
    statement = plan.statement
    table_name = statement.schema.table_name
    condition = statement.where

    if plan.route is AccessRoute.SCAN:
        measured = measure_native(table, table.scan)
        return measured.value, Step(
            op=Op.SCAN,
            structure=plan.table.structure,
            table=table_name,
            stats=measured.stats,
            time_ms=measured.time_ms,
        )

    if condition is None:
        raise ValueError(f"la ruta {plan.route.value} necesita una condicion")
    detail = _condition_text(condition, source)
    column_name = condition.column.column.name

    if plan.route is AccessRoute.TABLE_SEARCH:
        key = equality_key(condition, native)
        measured = measure_native(table, lambda: table.search(key))
        return measured.value, Step(
            op=Op.SEARCH,
            structure=plan.table.structure,
            table=table_name,
            column=column_name,
            detail=detail,
            stats=measured.stats,
            time_ms=measured.time_ms,
        )

    if plan.route is AccessRoute.TABLE_RANGE:
        lo, hi = range_values(condition, native)
        measured = measure_native(table, lambda: table.range_search(lo, hi))
        return measured.value, Step(
            op=Op.RANGE_SEARCH,
            structure=plan.table.structure,
            table=table_name,
            column=column_name,
            detail=detail,
            stats=measured.stats,
            time_ms=measured.time_ms,
        )

    index_metadata = plan.index
    if index_metadata is None:  # protegido tambien por PhysicalSelectPlan
        raise ValueError(f"la ruta {plan.route.value} necesita un indice")
    index = database.index(table_name, index_metadata.name)

    if plan.route is AccessRoute.INDEX_SEARCH:
        key = equality_key(condition, native)
        index_measurement = measure_native(index, lambda: index.search(key))
        index_op = Op.INDEX_SEARCH
    elif plan.route is AccessRoute.INDEX_RANGE:
        if not index.supports_range():
            raise RuntimeError(
                f"el indice {index_metadata.name!r} fue planificado para rango, pero no lo soporta"
            )
        lo, hi = range_values(condition, native)
        index_measurement = measure_native(index, lambda: index.range_search(lo, hi))
        index_op = Op.INDEX_RANGE
    else:
        raise ValueError(f"ruta de SELECT desconocida: {plan.route!r}")

    index_step = Step(
        op=index_op,
        structure=index_metadata.structure,
        table=table_name,
        column=column_name,
        detail=detail,
        stats=index_measurement.stats,
        time_ms=index_measurement.time_ms,
    )
    rids = index_measurement.value

    def fetch() -> list[Any]:
        records: list[Any] = []
        for rid in rids:
            record = table.read(rid)
            if record is None:
                raise RuntimeError(
                    f"el indice {index_metadata.name!r} de {table_name!r} "
                    f"apunta a un RID inexistente: {rid!r}"
                )
            records.append(record)
        return records

    fetch_measurement = measure_native(table, fetch)
    return fetch_measurement.value, Step(
        op=Op.FETCH,
        structure=plan.table.structure,
        table=table_name,
        detail=f"lee {len(rids)} registros por RID",
        stats=fetch_measurement.stats,
        time_ms=fetch_measurement.time_ms,
        children=[index_step],
    )


def _validate_plan(plan: PhysicalSelectPlan) -> None:
    condition = plan.statement.where
    if plan.residual_filter and condition is None:
        raise ValueError("un filtro residual necesita una condicion")
    if plan.route is AccessRoute.SCAN and condition is not None and not plan.residual_filter:
        raise ValueError("un scan con WHERE necesita un filtro residual")


def _condition_text(condition: BoundCondition, source: str) -> str:
    return source[condition.span.start : condition.span.end]


__all__ = ["SelectExecution", "execute_select"]
