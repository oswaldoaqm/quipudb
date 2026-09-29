"""Contexto para visualizar una consulta, construido del AST ya validado."""

from engine.executor.result import RadiusContext
from engine.parser.ast import DistanceMetric
from engine.parser.bound_ast import BoundDistanceCondition, BoundSelectStatement, BoundTableRef


def spatial_context_of(statement: BoundSelectStatement) -> RadiusContext | None:
    condition = statement.where
    if not isinstance(condition, BoundDistanceCondition):
        return None
    # Una fuente compuesta/agrupada no garantiza identidad de registros de una
    # sola tabla en la salida. Conservamos la region, sin habilitar emparejamiento.
    table = (
        statement.source.schema.table_name
        if isinstance(statement.source, BoundTableRef) and statement.group_by is None
        else None
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
