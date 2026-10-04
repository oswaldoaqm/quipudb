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
    BoundPolygonCondition,
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
    if isinstance(condition, BoundPolygonCondition):
        if not isinstance(value, PointValue):
            raise TypeError("DENTRO solo se puede evaluar sobre un POINT")
        return contains_point(condition.vertices, value)
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


def polygon_search_args(condition: BoundCondition, native: Any) -> list[object]:
    """Traduce DENTRO a la lista de vertices que espera el R-Tree nativo."""

    if not isinstance(condition, BoundPolygonCondition):
        raise TypeError("una busqueda por poligono necesita DENTRO")
    return [to_native_value(vertex, native) for vertex in condition.vertices]


def contains_point(vertices: tuple[PointValue, ...], point: PointValue) -> bool:
    """Si ``point`` cae dentro del poligono o sobre su borde.

    Es una traduccion literal de ``contains_point`` del core (``rtree.cpp``),
    con las mismas tolerancias y el mismo orden de operaciones, para que una
    consulta sin indice decida el borde exactamente igual que el R-Tree. Como
    en el core, ``x`` es la longitud e ``y`` la latitud.
    """

    if len(vertices) < 3:
        raise ValueError(f"un poligono necesita al menos 3 vertices, y se dieron {len(vertices)}")
    px, py = point.longitude, point.latitude
    polygon = [(vertex.longitude, vertex.latitude) for vertex in vertices]

    # El borde primero: el conteo de cruces no decide de forma estable sobre una arista.
    j = len(polygon) - 1
    for i in range(len(polygon)):
        if _on_segment(polygon[j], polygon[i], px, py):
            return True
        j = i

    inside = False
    j = len(polygon) - 1
    for i in range(len(polygon)):
        ax, ay = polygon[i]
        bx, by = polygon[j]
        if (ay > py) != (by > py) and px < (bx - ax) * (py - ay) / (by - ay) + ax:
            inside = not inside
        j = i
    return inside


def _on_segment(a: tuple[float, float], b: tuple[float, float], px: float, py: float) -> bool:
    ax, ay = a
    bx, by = b
    scale = max(abs(ax), abs(ay), abs(bx), abs(by), abs(px), abs(py), 1.0)
    cross = (bx - ax) * (py - ay) - (by - ay) * (px - ax)
    if abs(cross) > scale * scale * 1e-12:
        return False
    slack = scale * 1e-12
    return (
        min(ax, bx) - slack <= px <= max(ax, bx) + slack
        and min(ay, by) - slack <= py <= max(ay, by) + slack
    )


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
    "contains_point",
    "distance_between",
    "equality_key",
    "matches",
    "native_metric",
    "polygon_search_args",
    "range_values",
    "spatial_search_args",
]
