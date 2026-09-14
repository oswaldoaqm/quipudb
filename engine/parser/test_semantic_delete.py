"""Pruebas del enlace semantico puro de DELETE."""

from dataclasses import FrozenInstanceError, replace
from datetime import date

import pytest

from engine.parser import DeleteStatement, parse_sql
from engine.parser.ast import ComparisonOperator, SqlTypeName
from engine.parser.bound_ast import (
    BoundBetweenCondition,
    BoundColumn,
    BoundComparisonCondition,
    BoundDeleteStatement,
    BoundSchema,
)
from engine.parser.errors import SQLSemanticError
from engine.parser.semantic import bind_delete


def _delete(source: str) -> DeleteStatement:
    statement = parse_sql(source)
    assert isinstance(statement, DeleteStatement)
    return statement


def _schema() -> BoundSchema:
    return BoundSchema(
        "datos",
        (
            BoundColumn("id", SqlTypeName.INT, None),
            BoundColumn("promedio", SqlTypeName.DOUBLE, None),
            BoundColumn("nombre", SqlTypeName.VARCHAR, 8),
            BoundColumn("activo", SqlTypeName.BOOL, None),
            BoundColumn("ingreso", SqlTypeName.DATE, None),
        ),
        key_column=0,
    )


@pytest.mark.parametrize(
    ("condition", "column", "operator", "expected"),
    [
        ("id = -7", 0, ComparisonOperator.EQUAL, -7),
        ("promedio >= 17", 1, ComparisonOperator.GREATER_THAN_OR_EQUAL, 17.0),
        ("nombre < 'Zoe'", 2, ComparisonOperator.LESS_THAN, "Zoe"),
        ("activo = TRUE", 3, ComparisonOperator.EQUAL, True),
        (
            "ingreso <= DATE '2026-09-14'",
            4,
            ComparisonOperator.LESS_THAN_OR_EQUAL,
            date(2026, 9, 14),
        ),
    ],
)
def test_bind_delete_resuelve_comparaciones_de_los_cinco_tipos(
    condition: str,
    column: int,
    operator: ComparisonOperator,
    expected: object,
) -> None:
    statement = _delete(f"DELETE FROM datos WHERE {condition}")

    bound = bind_delete(statement, _schema())

    assert isinstance(bound, BoundDeleteStatement)
    assert isinstance(bound.where, BoundComparisonCondition)
    assert bound.schema == _schema()
    assert bound.where.column.index == column
    assert bound.where.operator is operator
    assert bound.where.value == expected
    assert bound.span == statement.span
    with pytest.raises(FrozenInstanceError):
        bound.span = statement.table.span  # type: ignore[misc]


def test_bind_delete_between_es_inclusivo_y_admite_limites_invertidos() -> None:
    statement = _delete("DELETE FROM datos WHERE promedio BETWEEN 20 AND 10")

    bound = bind_delete(statement, _schema())

    assert isinstance(bound.where, BoundBetweenCondition)
    assert bound.where.column.index == 1
    assert bound.where.lower == 20.0
    assert bound.where.upper == 10.0


def test_bind_delete_rechaza_tabla_distinta_con_span_preciso() -> None:
    source = "DELETE FROM otra WHERE id = 1"
    statement = _delete(source)

    with pytest.raises(SQLSemanticError) as caught:
        bind_delete(statement, _schema(), source)

    assert caught.value.span == statement.table.span
    assert caught.value.source == source
    assert "otra" in caught.value.message
    assert "datos" in caught.value.message


def test_bind_delete_rechaza_columna_inexistente_y_tipo_incompatible() -> None:
    missing = _delete("DELETE FROM datos WHERE ausente = 1")
    wrong_type = _delete("DELETE FROM datos WHERE activo = 1")

    with pytest.raises(SQLSemanticError) as missing_error:
        bind_delete(missing, _schema())
    with pytest.raises(SQLSemanticError) as type_error:
        bind_delete(wrong_type, _schema())

    assert missing_error.value.span == missing.where.column.span
    assert type_error.value.span == wrong_type.where.value.span  # type: ignore[union-attr]
    assert "se esperaba BOOL" in type_error.value.message


def test_bind_delete_defiende_ast_manual_sin_where() -> None:
    statement = replace(_delete("DELETE FROM datos WHERE id = 1"), where=None)  # type: ignore[arg-type]

    with pytest.raises(SQLSemanticError, match="requiere una clausula WHERE") as caught:
        bind_delete(statement, _schema())

    assert caught.value.span == statement.span
