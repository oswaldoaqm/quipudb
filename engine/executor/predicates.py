"""Conversion y evaluacion compartida de predicados enlazados."""

from __future__ import annotations

import math
from typing import Any

from engine.executor.native import native_range_bounds, to_native_value
from engine.parser.ast import ComparisonOperator, DistanceMetric
from engine.parser.bound_ast import (
    BoundBetweenCondition,
    BoundComparisonCondition,
    BoundCondition,
    BoundDistanceCondition,
    BoundValue,
    PointValue,
)

_EARTH_RADIUS_METERS = 6_371_008.8


def equality_key(condition: BoundCondition, native: Any) -> object:
    """Convierte la igualdad a la clave esperada por el core."""

    if not isinstance(condition, BoundComparisonCondition):
        raise TypeError("una busqueda puntual necesita una comparacion")
    if condition.operator is not ComparisonOperator.EQUAL:
        raise ValueError("una busqueda puntual necesita el operador =")
    return to_native_value(condition.value, native)


def range_values(condition: BoundCondition, native: Any) -> tuple[object, object]:
    """Construye los extremos inclusivos que aceptan tabla e indice."""

    if isinstance(condition, BoundBetweenCondition):
        return (
            to_native_value(condition.lower, native),
            to_native_value(condition.upper, native),
        )

    if not isinstance(condition, BoundComparisonCondition):
        raise TypeError(f"condicion enlazada desconocida: {type(condition).__name__}")
    if condition.operator is ComparisonOperator.EQUAL:
        raise ValueError("una busqueda por rango no admite el operador =")

    value = to_native_value(condition.value, native)
    lower, upper = native_range_bounds(condition.column.column, native)
    if condition.operator in {
        ComparisonOperator.LESS_THAN,
        ComparisonOperator.LESS_THAN_OR_EQUAL,
    }:
        return lower, value
    if condition.operator in {
        ComparisonOperator.GREATER_THAN,
        ComparisonOperator.GREATER_THAN_OR_EQUAL,
    }:
        return value, upper
    raise ValueError(f"operador de rango desconocido: {condition.operator!r}")


def matches(row: tuple[BoundValue, ...], condition: BoundCondition) -> bool:
    """Evalua en memoria un predicado sobre una fila ya convertida."""

    value = row[condition.column.index]
    if isinstance(condition, BoundDistanceCondition):
        if not isinstance(value, PointValue):
            raise TypeError("DISTANCIA solo se puede evaluar sobre un POINT")
        distance = distance_between(value, condition.center, condition.metric)
        if condition.operator is ComparisonOperator.LESS_THAN:
            return distance < condition.radius
        if condition.operator is ComparisonOperator.LESS_THAN_OR_EQUAL:
            return distance <= condition.radius
        raise ValueError(f"operador espacial desconocido: {condition.operator!r}")
    if isinstance(condition, BoundBetweenCondition):
        return _compare(value, condition.lower) >= 0 and _compare(value, condition.upper) <= 0

    comparison = _compare(value, condition.value)
    return {
        ComparisonOperator.EQUAL: comparison == 0,
        ComparisonOperator.LESS_THAN: comparison < 0,
        ComparisonOperator.LESS_THAN_OR_EQUAL: comparison <= 0,
        ComparisonOperator.GREATER_THAN: comparison > 0,
        ComparisonOperator.GREATER_THAN_OR_EQUAL: comparison >= 0,
    }[condition.operator]


def spatial_search_args(condition: BoundCondition, native: Any) -> tuple[object, float, Any]:
    """Traduce el predicado espacial a los argumentos del R-Tree nativo."""

    if not isinstance(condition, BoundDistanceCondition):
        raise TypeError("una busqueda por radio necesita DISTANCIA")
    metric = native_metric(condition.metric, native)
    return to_native_value(condition.center, native), condition.radius, metric


def native_metric(metric: DistanceMetric, native: Any) -> Any:
    """Misma eleccion de metrica para radio, k-NN y ordenamiento."""

    return {
        DistanceMetric.HAVERSINE: native.Metric.HAVERSINE,
        DistanceMetric.EUCLIDEAN: native.Metric.EUCLIDEAN,
    }[metric]


def distance_between(left: PointValue, right: PointValue, metric: DistanceMetric) -> float:
    if metric is DistanceMetric.EUCLIDEAN:
        return math.hypot(left.longitude - right.longitude, left.latitude - right.latitude)

    latitude_left = math.radians(left.latitude)
    latitude_right = math.radians(right.latitude)
    delta_latitude = latitude_right - latitude_left
    delta_longitude = math.radians(right.longitude - left.longitude)
    haversine = (
        math.sin(delta_latitude / 2.0) ** 2
        + math.cos(latitude_left)
        * math.cos(latitude_right)
        * math.sin(delta_longitude / 2.0) ** 2
    )
    central_angle = 2.0 * math.asin(math.sqrt(min(1.0, haversine)))
    return _EARTH_RADIUS_METERS * central_angle


def _compare(left: BoundValue, right: BoundValue) -> int:
    """Compara sin depender de ``<=``; el Date nativo solo expone ``<``."""

    if left < right:
        return -1
    if right < left:
        return 1
    return 0


__all__ = [
    "distance_between",
    "equality_key",
    "matches",
    "native_metric",
    "range_values",
    "spatial_search_args",
]
