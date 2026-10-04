"""Contexto para visualizar una consulta, construido del AST ya validado."""

from engine.executor.result import PolygonContext, RadiusContext, SpatialContext
from engine.parser.ast import DistanceMetric
from engine.parser.bound_ast import (
    BoundDistanceCondition,
    BoundPolygonCondition,
    BoundSelectStatement,
    BoundTableRef,
)


def spatial_context_of(statement: BoundSelectStatement) -> SpatialContext | None:
    condition = statement.where
    if not isinstance(condition, (BoundDistanceCondition, BoundPolygonCondition)):
        return None
    # Una fuente compuesta/agrupada no garantiza identidad de registros de una
    # sola tabla en la salida. Conservamos la region, sin habilitar emparejamiento.
    table = (
        statement.source.schema.table_name
        if isinstance(statement.source, BoundTableRef) and statement.group_by is None
        else None
    )
    if isinstance(condition, BoundPolygonCondition):
        return PolygonContext(
            table=table,
            column=condition.column.column.name,
            vertices=condition.vertices,
        )
    return RadiusContext(
        table=table,
        column=condition.column.column.name,
        center=condition.center,
        radius=condition.radius,
        metric=condition.metric,
        unit="meters" if condition.metric is DistanceMetric.HAVERSINE else "degrees",
        operator=condition.operator,
    )
