"""Pipelines medidos de ``ORDER BY`` y ``GROUP BY`` sobre el core externo."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter_ns
from typing import Any

from engine.executor.instrumentation import copy_stats, measure_memory, measure_native
from engine.executor.native import from_native_record, from_native_schema, to_native_schema
from engine.executor.operators import SelectExecution, execute_source
from engine.executor.predicates import equality_key, matches, range_values
from engine.parser.ast import AggregateFunction, OrderDirection
from engine.parser.bound_ast import (
    BoundColumnReference,
    BoundCondition,
    BoundSchema,
)
from engine.planner.optimizer import (
    AccessRoute,
    GroupStrategy,
    PhysicalJoin,
    PhysicalSelectPlan,
)
from engine.planner.plan import Op, Stats, Step, Structure


@dataclass(frozen=True, slots=True)
class ExternalExecutionOptions:
    """Memoria y temporales disponibles para los algoritmos externos."""

    buffers: int = 64
    page_size: int = 4096
    temp_dir: Path | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.buffers, int) or isinstance(self.buffers, bool):
            raise TypeError("external_buffers debe ser un entero mayor o igual que 3")
        if self.buffers < 3:
            raise ValueError("external_buffers debe ser un entero mayor o igual que 3")
        if not isinstance(self.page_size, int) or isinstance(self.page_size, bool):
            raise TypeError("external_page_size debe ser un entero entre 128 y 65536")
        if not 128 <= self.page_size <= 65536:
            raise ValueError("external_page_size debe estar entre 128 y 65536")
        if self.temp_dir is not None:
            object.__setattr__(self, "temp_dir", Path(self.temp_dir))


@dataclass(slots=True)
class _Timer:
    elapsed_ns: int = 0

    @property
    def time_ms(self) -> float:
        return self.elapsed_ns / 1_000_000


class _TimedIterator:
    """Atribuye a su hijo el tiempo consumido llamada por llamada."""

    def __init__(self, source: Iterable[Any], timer: _Timer) -> None:
        self._iterator = iter(source)
        self._timer = timer

    def __iter__(self) -> _TimedIterator:
        return self

    def __next__(self) -> Any:
        started_ns = perf_counter_ns()
        try:
            return next(self._iterator)
        finally:
            self._timer.elapsed_ns += perf_counter_ns() - started_ns


@dataclass(slots=True)
class _CandidatePipeline:
    source: Iterable[Any]
    root: Step
    timers: list[_Timer] = field(default_factory=list)
    finalizers: list[Callable[[], None]] = field(default_factory=list)

    @property
    def stream_time_ms(self) -> float:
        return sum(timer.time_ms for timer in self.timers)

    def finalize(self) -> None:
        for finalize in self.finalizers:
            finalize()


def execute_external_select(
    database: Any,
    native: Any,
    plan: PhysicalSelectPlan,
    source: str,
    options: ExternalExecutionOptions,
) -> SelectExecution:
    """Ejecuta SELECT externo sin agregar una lista a las rutas incrementales."""

    statement = plan.statement
    pipeline = _candidate_pipeline(database, native, plan, source, options)
    try:
        if statement.group_by is not None:
            return _execute_grouped(native, statement.schema, plan, pipeline, options)
        if statement.order_by is None:
            raise ValueError("el pipeline externo necesita ORDER BY o GROUP BY")
        return _execute_ordered(native, statement.schema, plan, pipeline, options)
    finally:
        pipeline.finalize()


def _execute_ordered(
    native: Any,
    schema: BoundSchema,
    plan: PhysicalSelectPlan,
    pipeline: _CandidatePipeline,
    options: ExternalExecutionOptions,
) -> SelectExecution:
    statement = plan.statement
    order_by = statement.order_by
    if order_by is None:
        raise ValueError("ORDER BY no fue resuelto")

    native_rows, sort_step = _sort(
        native,
        schema,
        pipeline.source,
        order_by.column.index,
        order_by.direction,
        statement.schema.table_name,
        order_by.column.column.name,
        pipeline.root,
        lambda: pipeline.stream_time_ms,
        options,
    )
    rows = tuple(from_native_record(row, schema) for row in native_rows)
    columns = tuple(projection.column.name for projection in statement.projections)

    if statement.wildcard:
        return SelectExecution(columns, rows, sort_step)

    projected = measure_memory(
        lambda: tuple(
            tuple(row[projection.index] for projection in statement.projections) for row in rows
        ),
        records_examined=len(rows),
        records_returned=len,
    )
    project_step = Step(
        op=Op.PROJECT,
        structure=Structure.MEMORY,
        table=schema.table_name,
        detail=", ".join(columns),
        stats=projected.stats,
        time_ms=projected.time_ms,
        children=[sort_step],
    )
    return SelectExecution(columns, projected.value, project_step)


def _execute_grouped(
    native: Any,
    schema: BoundSchema,
    plan: PhysicalSelectPlan,
    pipeline: _CandidatePipeline,
    options: ExternalExecutionOptions,
) -> SelectExecution:
    statement = plan.statement
    group_by = statement.group_by
    if group_by is None:
        raise ValueError("GROUP BY no fue resuelto")

    aggregate_specs: list[Any] = []
    output_indexes: list[int] = []
    next_aggregate = 1
    aggregate_values = {
        AggregateFunction.SUM: native.Aggregate.SUM,
        AggregateFunction.MIN: native.Aggregate.MIN,
        AggregateFunction.MAX: native.Aggregate.MAX,
        AggregateFunction.AVG: native.Aggregate.AVG,
    }
    for projection in statement.projections:
        if isinstance(projection, BoundColumnReference):
            output_indexes.append(0)
            continue
        if projection.function is AggregateFunction.COUNT:
            aggregate_specs.append(native.AggregateSpec.count())
        else:
            argument = projection.argument
            if argument is None:
                raise ValueError(f"{projection.function.value} necesita una columna")
            aggregate_specs.append(
                native.AggregateSpec.of(aggregate_values[projection.function], argument.index)
            )
        output_indexes.append(next_aggregate)
        next_aggregate += 1

    native_schema = to_native_schema(schema, native)
    requested_strategy = plan.group_strategy
    if requested_strategy is None:
        raise ValueError("GROUP BY necesita una estrategia fisica")
    native_strategy = {
        GroupStrategy.AUTO: native.ExternalGroupBy.Strategy.AUTO,
        GroupStrategy.HASH: native.ExternalGroupBy.Strategy.HASH,
    }[requested_strategy]
    group = native.ExternalGroupBy(
        native_schema,
        group_by.column.index,
        aggregate_specs,
        native_strategy,
        buffers=options.buffers,
        page_size=options.page_size,
        dir=_native_dir(options),
    )
    native_source = native.source_of(pipeline.source)
    before_pipeline_ms = pipeline.stream_time_ms
    started_ns = perf_counter_ns()
    grouped_source = group.grouped(native_source)
    group_call_ms = (perf_counter_ns() - started_ns) / 1_000_000

    grouped_schema = from_native_schema(group.output_schema(), native)
    group_output_timer = _Timer()
    measured_group_output = _TimedIterator(grouped_source, group_output_timer)
    used = group.used()

    order_by = statement.order_by
    reuses_group_order = (
        order_by is not None
        and order_by.direction is OrderDirection.ASC
        and used == native.ExternalGroupBy.Strategy.SORT
    )
    if order_by is None or reuses_group_order:
        native_rows = tuple(measured_group_output)
        root: Step | None = None
    else:
        native_rows, root = _sort(
            native,
            grouped_schema,
            measured_group_output,
            0,
            order_by.direction,
            schema.table_name,
            group_by.column.column.name,
            None,
            lambda: group_output_timer.time_ms,
            options,
        )

    pipeline_ms = pipeline.stream_time_ms - before_pipeline_ms
    group_time_ms = max(group_call_ms + group_output_timer.time_ms - pipeline_ms, 0.0)
    used_hash = used == native.ExternalGroupBy.Strategy.HASH
    fallback = bool(group.fell_back())
    fallback_detail = "con fallback a sort" if fallback else "sin fallback"
    if used_hash:
        reason = "hash porque las particiones cupieron durante la agregacion"
    else:
        reason = "sort porque el hash no convergio al reparticionar"
    if reuses_group_order:
        reason += "; ORDER BY ASC reutiliza la salida ya ordenada"
    group_stats = copy_stats(group.stats())
    group_stats.records_examined = int(group.rows())
    group_stats.records_returned = int(group.groups())
    group_step = Step(
        op=Op.GROUP,
        structure=Structure.EXTERNAL_HASH if used_hash else Structure.EXTERNAL_SORT,
        table=schema.table_name,
        column=group_by.column.column.name,
        detail=(
            f"estrategia solicitada {requested_strategy.value.upper()}; usada "
            f"{_strategy_name(used)}; {group.partitions()} particiones; "
            f"{group.repartitions()} reparticiones; {fallback_detail}; {reason}"
        ),
        stats=group_stats,
        time_ms=group_time_ms,
        children=[pipeline.root],
    )
    if root is None:
        root = group_step
    else:
        root.children = [group_step]

    converted = tuple(from_native_record(row, grouped_schema) for row in native_rows)
    columns = tuple(grouped_schema.columns[index].name for index in output_indexes)
    projected = measure_memory(
        lambda: tuple(tuple(row[index] for index in output_indexes) for row in converted),
        records_examined=len(converted),
        records_returned=len,
    )
    project_step = Step(
        op=Op.PROJECT,
        structure=Structure.MEMORY,
        table=schema.table_name,
        detail=", ".join(columns),
        stats=projected.stats,
        time_ms=projected.time_ms,
        children=[root],
    )
    execution = SelectExecution(columns, projected.value, project_step)
    del measured_group_output, grouped_source, native_source, group
    return execution


def _sort(
    native: Any,
    schema: BoundSchema,
    records: Iterable[Any],
    key_column: int,
    direction: OrderDirection,
    table_name: str,
    column_name: str,
    child: Step | None,
    upstream_time_ms: Callable[[], float],
    options: ExternalExecutionOptions,
) -> tuple[tuple[Any, ...], Step]:
    native_direction = {
        OrderDirection.ASC: native.ExternalSort.Direction.ASC,
        OrderDirection.DESC: native.ExternalSort.Direction.DESC,
    }[direction]
    sorter = native.ExternalSort(
        to_native_schema(schema, native),
        key_column,
        buffers=options.buffers,
        page_size=options.page_size,
        dir=_native_dir(options),
        direction=native_direction,
    )
    native_source = native.source_of(records)
    started_ns = perf_counter_ns()
    output = sorter.sorted(native_source)
    rows = tuple(output)
    elapsed_ms = (perf_counter_ns() - started_ns) / 1_000_000

    runs = sorter.runs()
    passes = sorter.passes()
    buffers = sorter.buffers()
    stats = copy_stats(sorter.stats())
    stats.records_examined = len(rows)
    stats.records_returned = len(rows)
    if runs == 0:
        reason = "todo cupo en memoria; no se crearon runs"
    else:
        reason = "se usaron runs en disco porque la entrada supero la memoria"
    step = Step(
        op=Op.SORT,
        structure=Structure.EXTERNAL_SORT,
        table=table_name,
        column=column_name,
        detail=(
            f"direccion {direction.value}; {buffers} buffers; "
            f"{runs} runs; {passes} pasadas; {reason}"
        ),
        stats=stats,
        time_ms=max(elapsed_ms - upstream_time_ms(), 0.0),
        children=[] if child is None else [child],
    )
    del output, native_source, sorter
    return rows, step


def _candidate_pipeline(
    database: Any,
    native: Any,
    plan: PhysicalSelectPlan,
    source: str,
    options: ExternalExecutionOptions,
) -> _CandidatePipeline:
    statement = plan.statement

    if isinstance(plan.source, PhysicalJoin):
        # El join ya produce un flujo con su propio Step medido; ordenar o
        # agrupar por encima solo tiene que consumirlo. Las rutas de una sola
        # tabla siguen siendo incrementales y no pasan por aqui.
        rows, step = execute_source(
            database,
            native,
            plan.source,
            statement.source,
            statement.where,
            source,
            offset=0,
            temp_dir=options.temp_dir,
        )
        return _CandidatePipeline(source=native.source_of(rows), root=step)

    table_name = statement.schema.table_name
    table = database.table(table_name)
    condition = statement.where

    if plan.route is AccessRoute.SCAN:
        table.reset_stats()
        timer = _Timer()
        scan_step = Step(op=Op.SCAN, structure=plan.table.structure, table=table_name)
        pipeline = _CandidatePipeline(
            source=_TimedIterator(native.source_of(table), timer),
            root=scan_step,
            timers=[timer],
        )

        def finalize_scan() -> None:
            scan_step.stats = copy_stats(table.stats())
            scan_step.time_ms = timer.time_ms

        pipeline.finalizers.append(finalize_scan)
    elif plan.route in {AccessRoute.TABLE_SEARCH, AccessRoute.TABLE_RANGE}:
        if condition is None:
            raise ValueError(f"la ruta {plan.route.value} necesita una condicion")
        if plan.route is AccessRoute.TABLE_SEARCH:
            measured = measure_native(table, lambda: table.search(equality_key(condition, native)))
            operation = Op.SEARCH
        else:
            lower, upper = range_values(condition, native)
            measured = measure_native(table, lambda: table.range_search(lower, upper))
            operation = Op.RANGE_SEARCH
        pipeline = _CandidatePipeline(
            source=measured.value,
            root=Step(
                op=operation,
                structure=plan.table.structure,
                table=table_name,
                column=condition.column.column.name,
                detail=_condition_text(condition, source),
                stats=measured.stats,
                time_ms=measured.time_ms,
            ),
        )
    else:
        pipeline = _index_pipeline(database, native, table, plan, source)

    if plan.residual_filter:
        if condition is None:
            raise ValueError("un filtro residual necesita una condicion")
        pipeline = _with_filter(pipeline, statement.schema, condition, source)
    return pipeline


def _index_pipeline(
    database: Any,
    native: Any,
    table: Any,
    plan: PhysicalSelectPlan,
    source: str,
) -> _CandidatePipeline:
    statement = plan.statement
    table_name = statement.schema.table_name
    condition = statement.where
    metadata = plan.index
    if condition is None or metadata is None:
        raise ValueError(f"la ruta {plan.route.value} necesita condicion e indice")
    index = database.index(table_name, metadata.name)

    if plan.route is AccessRoute.INDEX_SEARCH:
        measured = measure_native(index, lambda: index.search(equality_key(condition, native)))
        operation = Op.INDEX_SEARCH
    elif plan.route is AccessRoute.INDEX_RANGE:
        lower, upper = range_values(condition, native)
        measured = measure_native(index, lambda: index.range_search(lower, upper))
        operation = Op.INDEX_RANGE
    else:
        raise ValueError(f"ruta externa desconocida: {plan.route!r}")

    index_step = Step(
        op=operation,
        structure=metadata.structure,
        table=table_name,
        column=condition.column.column.name,
        detail=_condition_text(condition, source),
        stats=measured.stats,
        time_ms=measured.time_ms,
    )
    rids = measured.value
    table.reset_stats()
    timer = _Timer()

    def fetched() -> Iterator[Any]:
        for rid in rids:
            record = table.read(rid)
            if record is None:
                raise RuntimeError(
                    f"el indice {metadata.name!r} de {table_name!r} "
                    f"apunta a un RID inexistente: {rid!r}"
                )
            yield record

    fetch_step = Step(
        op=Op.FETCH,
        structure=plan.table.structure,
        table=table_name,
        detail=f"lee {len(rids)} registros por RID",
        children=[index_step],
    )
    pipeline = _CandidatePipeline(
        source=_TimedIterator(fetched(), timer),
        root=fetch_step,
        timers=[timer],
    )

    def finalize_fetch() -> None:
        fetch_step.stats = copy_stats(table.stats())
        fetch_step.time_ms = timer.time_ms

    pipeline.finalizers.append(finalize_fetch)
    return pipeline


def _with_filter(
    pipeline: _CandidatePipeline,
    schema: BoundSchema,
    condition: BoundCondition,
    source: str,
) -> _CandidatePipeline:
    timer = _Timer()
    examined = 0
    returned = 0
    upstream = pipeline.source

    def filtered() -> Iterator[Any]:
        nonlocal examined, returned
        for row in upstream:
            started_ns = perf_counter_ns()
            accepted = False
            try:
                accepted = matches(from_native_record(row, schema), condition)
                examined += 1
                if accepted:
                    returned += 1
            finally:
                timer.elapsed_ns += perf_counter_ns() - started_ns
            if accepted:
                yield row

    filter_step = Step(
        op=Op.FILTER,
        structure=Structure.MEMORY,
        table=schema.table_name,
        column=condition.column.column.name,
        detail=_condition_text(condition, source),
        children=[pipeline.root],
    )
    next_pipeline = _CandidatePipeline(
        source=filtered(),
        root=filter_step,
        timers=[*pipeline.timers, timer],
        finalizers=list(pipeline.finalizers),
    )

    def finalize_filter() -> None:
        filter_step.stats = Stats(records_examined=examined, records_returned=returned)
        filter_step.time_ms = timer.time_ms

    next_pipeline.finalizers.append(finalize_filter)
    return next_pipeline


def _strategy_name(strategy: Any) -> str:
    text = str(strategy)
    return text.rsplit(".", 1)[-1].upper()


def _native_dir(options: ExternalExecutionOptions) -> str:
    return "" if options.temp_dir is None else str(options.temp_dir)


def _condition_text(condition: BoundCondition, source: str) -> str:
    return source[condition.span.start : condition.span.end]


__all__ = ["ExternalExecutionOptions", "execute_external_select"]
