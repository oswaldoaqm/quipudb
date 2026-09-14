"""Conversion y evaluacion compartida de predicados enlazados."""

from __future__ import annotations

from typing import Any

from engine.executor.native import native_range_bounds, to_native_value
from engine.parser.ast import ComparisonOperator
from engine.parser.bound_ast import (
    BoundBetweenCondition,
    BoundComparisonCondition,
    BoundCondition,
    BoundValue,
)


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


def _compare(left: BoundValue, right: BoundValue) -> int:
    """Compara sin depender de ``<=``; el Date nativo solo expone ``<``."""

    if left < right:
        return -1
    if right < left:
        return 1
    return 0


__all__ = ["equality_key", "matches", "range_values"]
