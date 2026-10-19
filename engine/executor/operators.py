"""Operadores fisicos de ``SELECT`` y construccion de su plan medido."""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from engine.executor.instrumentation import measure_memory, measure_native
from engine.executor.native import (
    from_native_record,
    to_native_schema,
    to_native_value,
)
from engine.executor.predicates import (
    equality_key,
    matches,
    native_metric,
    polygon_search_args,
    range_values,
    spatial_search_args,
    text_search_args,
)
from engine.parser.ast import OrderDirection, SqlTypeName
from engine.parser.bound_ast import (
    BoundCondition,
    BoundJoinRef,
    BoundSchema,
    BoundSource,
    BoundValue,
)
from engine.planner.explain import score_order_detail, text_search_step
from engine.planner.optimizer import (
    AccessRoute,
    JoinStrategy,
    PhysicalJoin,
    PhysicalSelectPlan,
    PhysicalSource,
    PhysicalTableAccess,
)
from engine.planner.plan import Op, Step, Structure


@dataclass(frozen=True, slots=True)
class SelectExecution:
    """Salida materializada de SELECT antes de envolverla en ``Plan``."""

    columns: tuple[str, ...]
    column_types: tuple[SqlTypeName, ...]
    rows: tuple[tuple[BoundValue, ...], ...]
    root: Step


def execute_select(
    database: Any,
    native: Any,
    plan: PhysicalSelectPlan,
    source: str,
    temp_dir: Path | None = None,
) -> SelectExecution:
    """Ejecuta una ruta fisica y arma el arbol ``Step`` con medidas reales."""

    _validate_plan(plan)
    statement = plan.statement
    table_name = statement.schema.table_name

    # El puntaje de MATCH (#169) no esta en la tabla: lo entrega el indice de
    # texto, uno por RID, y viaja en una lista paralela a las filas.
    usa_puntaje = statement.score is not None or statement.score_order is not None
    scores: list[float] | None = [] if usa_puntaje else None
    native_rows, root = execute_source(
        database,
        native,
        plan.source,
        statement.source,
        statement.where,
        source,
        offset=0,
        temp_dir=temp_dir,
        scores=scores,
    )
    rows = tuple(from_native_record(row, statement.schema) for row in native_rows)
    if scores is not None and len(scores) != len(rows):
        raise RuntimeError("el indice de texto devolvio un puntaje por fila distinto de las filas")

    if statement.score_order is not None:
        rows, scores, root = _order_by_score(statement, rows, scores or [], root)

    columns = tuple(projection.column.name for projection in statement.projections)
    tipos = tuple(projection.column.data_type for projection in statement.projections)
    if not statement.wildcard or statement.score is not None:
        score = statement.score
        if score is not None:
            columns = columns[: score.position] + (score.name,) + columns[score.position :]
            tipos = tipos[: score.position] + (SqlTypeName.DOUBLE,) + tipos[score.position :]
        puntajes = scores or []

        def proyectar() -> tuple[tuple[BoundValue, ...], ...]:
            salida = []
            for i, row in enumerate(rows):
                fila = row if statement.wildcard else tuple(
                    row[projection.index] for projection in statement.projections
                )
                if score is not None:
                    fila = fila[: score.position] + (puntajes[i],) + fila[score.position :]
                salida.append(fila)
            return tuple(salida)

        projected = measure_memory(
            proyectar,
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

    return apply_limit(
        SelectExecution(columns, tipos, rows, root),
        (
            None
            if isinstance(plan.source, PhysicalTableAccess) and plan.source.nearest is not None
            else statement.limit
        ),
        table_name,
    )


def _order_by_score(
    statement: Any,
    rows: tuple[tuple[BoundValue, ...], ...],
    scores: list[float],
    root: Step,
) -> tuple[tuple[tuple[BoundValue, ...], ...], list[float], Step]:
    """``ORDER BY`` sobre el puntaje (#169), sin External Sorting.

    El indice entrega los documentos de mayor a menor puntaje: DESC es ese
    mismo orden y ASC es el inverso. El paso queda en el plan igual, para que
    se vea que el orden lo puso el indice.
    """

    if statement.score_order.direction is OrderDirection.DESC:
        ordenado = measure_memory(lambda: (rows, scores), records_examined=0, records_returned=len)
    else:
        ordenado = measure_memory(
            lambda: (rows[::-1], scores[::-1]),
            records_examined=len(rows),
            records_returned=lambda par: len(par[0]),
        )
    nuevas, nuevos = ordenado.value
    return nuevas, nuevos, Step(
        op=Op.SORT,
        structure=Structure.MEMORY,
        table=statement.schema.table_name,
        detail=score_order_detail(statement),
        stats=ordenado.stats,
        time_ms=ordenado.time_ms,
        children=[root],
    )


def apply_limit(
    execution: SelectExecution,
    limit: int | None,
    table_name: str,
) -> SelectExecution:
    """Corta el resultado final y agrega el paso ``limit`` medido al plan."""

    if limit is None:
        return execution
    measured = measure_memory(
        lambda: execution.rows[:limit],
        records_examined=min(limit, len(execution.rows)),
        records_returned=len,
    )
    return SelectExecution(
        execution.columns,
        execution.column_types,
        measured.value,
        Step(
            op=Op.LIMIT,
            structure=Structure.MEMORY,
            table=table_name,
            detail=f"maximo {limit} filas",
            stats=measured.stats,
            time_ms=measured.time_ms,
            children=[execution.root],
        ),
    )


def execute_source(
    database: Any,
    native: Any,
    fisica: PhysicalSource,
    semantica: BoundSource,
    condition: BoundCondition | None,
    source: str,
    offset: int,
    temp_dir: Path | None = None,
    scores: list[float] | None = None,
) -> tuple[list[Any], Step]:
    """Ejecuta un nodo de la fuente y devuelve sus filas nativas y su ``Step``.

    Si ``scores`` no es ``None``, la ruta de texto (#169) agrega ahi el puntaje
    de cada fila, en el mismo orden que las filas.

    Los dos arboles se recorren a la vez porque los esquemas viven en el
    semantico y las rutas en el fisico. ``offset`` es donde empieza este nodo
    dentro del esquema de salida, que es lo que traduce el indice de una
    columna del predicado a una posicion local.
    """

    if isinstance(fisica, PhysicalJoin):
        assert isinstance(semantica, BoundJoinRef)
        rows, step = _execute_join(
            database, native, fisica, semantica, condition, source, offset, temp_dir
        )
    else:
        assert isinstance(fisica, PhysicalTableAccess)
        table = database.table(semantica.schema.table_name)
        rows, step = _read_candidates(
            database, native, table, fisica, semantica.schema, condition, source, scores
        )

    if not fisica.residual_filter:
        return rows, step

    if condition is None:  # protegido tambien por _validate_plan
        raise ValueError("un filtro residual necesita una condicion")
    schema = semantica.schema
    local = _local_condition(condition, offset)
    convertidas = [from_native_record(row, schema) for row in rows]
    filtered = measure_memory(
        lambda: [
            row
            for row, convertida in zip(rows, convertidas, strict=True)
            if matches(convertida, local)
        ],
        records_examined=len(rows),
        records_returned=len,
    )
    return filtered.value, Step(
        op=Op.FILTER,
        structure=Structure.MEMORY,
        table=schema.table_name,
        column=condition.column.column.name,
        detail=_condition_text(condition, source),
        stats=filtered.stats,
        time_ms=filtered.time_ms,
        children=[step],
    )


def _local_condition(condition: BoundCondition, offset: int) -> BoundCondition:
    """El mismo predicado con su columna referida al esquema de este nodo."""

    if offset == 0:
        return condition
    column = replace(condition.column, index=condition.column.index - offset)
    return replace(condition, column=column)


def _execute_join(
    database: Any,
    native: Any,
    fisica: PhysicalJoin,
    semantica: BoundJoinRef,
    condition: BoundCondition | None,
    source: str,
    offset: int,
    temp_dir: Path | None,
) -> tuple[list[Any], Step]:
    left_rows, left_step = execute_source(
        database, native, fisica.left, semantica.left, condition, source, offset, temp_dir
    )
    right_rows, right_step = execute_source(
        database,
        native,
        fisica.right,
        semantica.right,
        condition,
        source,
        offset + len(semantica.left.schema.columns),
        temp_dir,
    )

    # Un directorio propio por join: sus particiones no tienen por que
    # compartir espacio de nombres con las de otra consulta, y asi se limpian
    # de una pieza aunque algo falle a media ejecucion. Desde el #98 el core ya
    # nombra sus temporales de forma unica por proceso, asi que esto es
    # aislamiento, no un parche.
    particiones = Path(tempfile.mkdtemp(prefix="quipudb_join_", dir=temp_dir))
    try:
        join = native.ExternalJoin(
            to_native_schema(semantica.left.schema, native),
            fisica.left_column,
            to_native_schema(semantica.right.schema, native),
            fisica.right_column,
            _native_strategy(fisica.strategy, native),
            dir=particiones,
        )
        izquierda = native.source_of(left_rows)

        def run() -> list[Any]:
            sonda = _probe_for(database, native, fisica, semantica)
            if sonda is None:
                return list(join.joined(izquierda, native.source_of(right_rows)))
            return list(
                join.joined(izquierda, sonda, native.source_of(right_rows), fisica.left_rows or 0)
            )

        measured = measure_memory(
            run,
            records_examined=len(left_rows) + len(right_rows),
            records_returned=len,
        )
        detalle = _join_detail(join, fisica)
        estructura = Structure(str(join.structure()))
    finally:
        shutil.rmtree(particiones, ignore_errors=True)

    return measured.value, Step(
        op=Op.JOIN,
        structure=estructura,
        table=semantica.schema.table_name,
        column=semantica.left_column.column.name,
        detail=detalle,
        stats=measured.stats,
        time_ms=measured.time_ms,
        children=[left_step, right_step],
    )


def _native_strategy(strategy: JoinStrategy, native: Any) -> Any:
    if strategy is JoinStrategy.AUTO:
        return native.ExternalJoin.Strategy.AUTO
    return native.ExternalJoin.Strategy.HASH


def _probe_for(
    database: Any,
    native: Any,
    fisica: PhysicalJoin,
    semantica: BoundJoinRef,
) -> Any:
    """La sonda del lado derecho, o ``None`` si el join va por hash."""

    if fisica.strategy is not JoinStrategy.AUTO:
        return None
    table_name = semantica.right.schema.table_name
    table = database.table(table_name)
    if fisica.probe is not None:
        return native.probe_of(database.index(table_name, fisica.probe.name), table)
    return native.probe_of(table)


def _join_detail(join: Any, fisica: PhysicalJoin) -> str:
    """Que se pidio, que eligio AUTO, y lo que costo el reparto de la clave."""

    hash_join = join.used() == type(join).Strategy.HASH
    externas = fisica.left_rows if fisica.left_rows is not None else "desconocidas"
    partes = [
        f"pedido {fisica.strategy.value}, usado {'hash join' if hash_join else 'index nested loop'}",
        f"filas externas {externas}",
        f"{join.left_rows()} x {join.right_rows()} -> {join.output_rows()} filas",
    ]
    if join.partitions():
        partes.append(f"{join.partitions()} particiones")
    if join.blocked_partitions():
        partes.append(f"{join.blocked_partitions()} recorridas por bloques")
    return "; ".join(partes)


def _read_candidates(
    database: Any,
    native: Any,
    table: Any,
    acceso: PhysicalTableAccess,
    schema: BoundSchema,
    condition: BoundCondition | None,
    source: str,
    scores: list[float] | None = None,
) -> tuple[Any, Step]:
    plan = acceso
    table_name = schema.table_name

    if plan.route is AccessRoute.SCAN:
        measured = measure_native(table, table.scan)
        return measured.value, Step(
            op=Op.SCAN,
            structure=acceso.table.structure,
            table=table_name,
            stats=measured.stats,
            time_ms=measured.time_ms,
        )

    if plan.nearest is not None:
        index = database.index(table_name, plan.index.name)
        center = to_native_value(plan.nearest.center, native)
        metric = native_metric(plan.nearest.metric, native)
        # LIMIT puede exceder size_t o la cardinalidad: no se reserva espacio
        # para mas vecinos que entradas existentes en el indice.
        k = min(plan.nearest_limit, len(index))
        nearest = measure_native(index, lambda: index.k_nearest(center, k, metric))
        index_step = Step(
            op=Op.KNN_SEARCH,
            structure=Structure.RTREE,
            table=table_name,
            column=plan.nearest.column.column.name,
            detail=(
                f"indice {plan.index.name}; k-NN k={plan.nearest_limit}; "
                f"{plan.nearest.metric.value}; "
                f"{source[plan.nearest.span.start:plan.nearest.span.end]}"
            ),
            stats=nearest.stats,
            time_ms=nearest.time_ms,
        )

        def fetch_nearest() -> list[Any]:
            rows = []
            for rid in nearest.value:
                record = table.read(rid)
                if record is None:
                    raise RuntimeError(f"el indice {plan.index.name!r} apunta a un RID inexistente")
                rows.append(record)
            return rows

        fetched = measure_native(table, fetch_nearest)
        return fetched.value, Step(
            op=Op.FETCH,
            structure=plan.table.structure,
            table=table_name,
            detail="lee por RID conservando el orden de los vecinos",
            stats=fetched.stats,
            time_ms=fetched.time_ms,
            children=[index_step],
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
            structure=acceso.table.structure,
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
            structure=acceso.table.structure,
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
    elif plan.route is AccessRoute.RTREE_RADIUS:
        center, radius, metric = spatial_search_args(condition, native)
        index_measurement = measure_native(
            index,
            lambda: index.search_radius(center, radius, metric),
        )
        index_op = Op.RADIUS_SEARCH
    elif plan.route is AccessRoute.RTREE_POLYGON:
        vertices = polygon_search_args(condition, native)
        index_measurement = measure_native(index, lambda: index.search_polygon(vertices))
        index_op = Op.POLYGON_SEARCH
    elif plan.route is AccessRoute.TEXT_SEARCH:
        # Los RID salen por relevancia, de mayor a menor, y el fetch conserva
        # ese orden: sin ORDER BY, es el orden del resultado.
        query, ranking, k = text_search_args(condition, native, plan.text_limit)

        def buscar() -> list[Any]:
            resultado = index.search_text(query, ranking, k)
            if scores is not None:
                scores.extend(score for _, score in resultado)
            return [rid for rid, _ in resultado]

        index_measurement = measure_native(index, buscar)
        index_op = Op.TEXT_SEARCH
    else:
        raise ValueError(f"ruta de SELECT desconocida: {plan.route!r}")

    if plan.route is AccessRoute.TEXT_SEARCH:
        index_step = text_search_step(
            plan,
            condition,
            source,
            table_name=table_name,
            stats=index_measurement.stats,
            time_ms=index_measurement.time_ms,
        )
    else:
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
        structure=acceso.table.structure,
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
    if condition is None or _consume_el_predicado(plan.source):
        return
    raise ValueError("un scan con WHERE necesita un filtro residual")


def _consume_el_predicado(source: PhysicalSource) -> bool:
    """Si alguna parte del plan se hace cargo del predicado.

    Una ruta que no sea scan lo resolvio en la estructura; un filtro residual
    lo evalua en memoria. En un join basta con que UNA hoja lo haga: las demas
    se recorren enteras a proposito.
    """

    if isinstance(source, PhysicalTableAccess):
        return source.route is not AccessRoute.SCAN or source.residual_filter
    return (
        source.residual_filter
        or _consume_el_predicado(source.left)
        or _consume_el_predicado(source.right)
    )


def _condition_text(condition: BoundCondition, source: str) -> str:
    return source[condition.span.start : condition.span.end]


__all__ = ["SelectExecution", "apply_limit", "execute_select", "execute_source"]
