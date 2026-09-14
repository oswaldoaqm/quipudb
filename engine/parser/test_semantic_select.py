"""Pruebas del enlace semantico puro de SELECT."""

from dataclasses import FrozenInstanceError, replace
from datetime import date

import pytest

from engine.parser import SelectStatement, Wildcard, parse_sql
from engine.parser.ast import ComparisonOperator, SqlTypeName
from engine.parser.bound_ast import (
    BoundBetweenCondition,
    BoundColumn,
    BoundColumnReference,
    BoundComparisonCondition,
    BoundSchema,
    BoundSelectStatement,
)
from engine.parser.errors import SQLSemanticError, SQLUnsupportedError
from engine.parser.semantic import bind_select


def _select(source: str) -> SelectStatement:
    statement = parse_sql(source)
    assert isinstance(statement, SelectStatement)
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
        0,
    )


def test_bind_select_expande_wildcard_y_conserva_esquema_y_span() -> None:
    statement = _select("SELECT * FROM datos")

    bound = bind_select(statement, _schema())

    assert isinstance(bound, BoundSelectStatement)
    assert bound.schema == _schema()
    assert bound.wildcard is True
    assert bound.where is None
    assert bound.span == statement.span
    assert [projection.index for projection in bound.projections] == [0, 1, 2, 3, 4]
    assert [projection.column.name for projection in bound.projections] == [
        "id",
        "promedio",
        "nombre",
        "activo",
        "ingreso",
    ]
    assert all(projection.span == statement.projections[0].span for projection in bound.projections)
    with pytest.raises(FrozenInstanceError):
        bound.wildcard = False  # type: ignore[misc]


def test_bind_select_preserva_orden_y_repeticiones_de_proyeccion() -> None:
    statement = _select("SELECT activo, id, activo FROM datos")

    bound = bind_select(statement, _schema())

    assert bound.wildcard is False
    assert [projection.index for projection in bound.projections] == [3, 0, 3]
    assert [projection.column.name for projection in bound.projections] == [
        "activo",
        "id",
        "activo",
    ]
    assert [projection.span for projection in bound.projections] == [
        projection.span for projection in statement.projections
    ]


def test_bind_select_resuelve_nombres_con_casing_exacto() -> None:
    schema = BoundSchema(
        "datos",
        (
            BoundColumn("id", SqlTypeName.INT, None),
            BoundColumn("ID", SqlTypeName.DOUBLE, None),
        ),
        0,
    )

    bound = bind_select(_select("SELECT ID, id FROM datos WHERE ID = 7"), schema)

    assert [projection.index for projection in bound.projections] == [1, 0]
    assert isinstance(bound.where, BoundComparisonCondition)
    assert bound.where.column.index == 1
    assert bound.where.value == 7.0
    assert type(bound.where.value) is float


def test_bind_select_rechaza_tabla_distinta_y_conserva_source() -> None:
    source = "SELECT * FROM Datos"
    statement = _select(source)

    with pytest.raises(SQLSemanticError) as caught:
        bind_select(statement, _schema(), source)

    assert caught.value.span == statement.table.span
    assert caught.value.source == source
    assert "Datos" in caught.value.message
    assert "datos" in caught.value.message
    assert "^" * len("Datos") in str(caught.value)


def test_bind_select_rechaza_columna_proyectada_inexistente_con_su_span() -> None:
    source = "SELECT ausente FROM datos"
    statement = _select(source)

    with pytest.raises(SQLSemanticError) as caught:
        bind_select(statement, _schema(), source)

    assert caught.value.span == statement.projections[0].span
    assert caught.value.source == source
    assert "ausente" in caught.value.message
    assert "^" * len("ausente") in str(caught.value)


def test_bind_select_rechaza_columna_de_where_inexistente_con_su_span() -> None:
    source = "SELECT id FROM datos WHERE ausente = 1"
    statement = _select(source)
    assert statement.where is not None

    with pytest.raises(SQLSemanticError) as caught:
        bind_select(statement, _schema(), source)

    assert caught.value.span == statement.where.column.span
    assert caught.value.source == source
    assert "ausente" in caught.value.message


@pytest.mark.parametrize(
    ("condition", "column_index", "operator", "expected"),
    [
        ("id = -7", 0, ComparisonOperator.EQUAL, -7),
        ("promedio >= 17", 1, ComparisonOperator.GREATER_THAN_OR_EQUAL, 17.0),
        ("nombre < 'Zoe'", 2, ComparisonOperator.LESS_THAN, "Zoe"),
        ("activo = TRUE", 3, ComparisonOperator.EQUAL, True),
        (
            "ingreso <= DATE '2026-09-13'",
            4,
            ComparisonOperator.LESS_THAN_OR_EQUAL,
            date(2026, 9, 13),
        ),
    ],
)
def test_bind_select_convierte_comparaciones_a_los_cinco_tipos(
    condition: str,
    column_index: int,
    operator: ComparisonOperator,
    expected: object,
) -> None:
    statement = _select(f"SELECT id FROM datos WHERE {condition}")

    bound = bind_select(statement, _schema())

    assert isinstance(bound.where, BoundComparisonCondition)
    assert bound.where.column.index == column_index
    assert bound.where.operator is operator
    assert bound.where.value == expected
    assert bound.where.span == statement.where.span  # type: ignore[union-attr]


def test_bind_select_between_es_inclusivo_y_no_rechaza_limites_invertidos() -> None:
    statement = _select("SELECT * FROM datos WHERE promedio BETWEEN 20 AND 10")

    bound = bind_select(statement, _schema())

    assert isinstance(bound.where, BoundBetweenCondition)
    assert bound.where.column == BoundColumnReference(
        1,
        _schema().columns[1],
        statement.where.column.span,  # type: ignore[union-attr]
    )
    assert bound.where.lower == 20.0
    assert bound.where.upper == 10.0
    assert type(bound.where.lower) is float
    assert type(bound.where.upper) is float
    assert bound.where.span == statement.where.span  # type: ignore[union-attr]


@pytest.mark.parametrize(
    ("condition", "expected_type", "received"),
    [
        ("id = 1.0", "INT", "DOUBLE"),
        ("promedio = TRUE", "DOUBLE", "BOOL"),
        ("nombre = 1", "VARCHAR", "INT"),
        ("activo = 1", "BOOL", "INT"),
        ("ingreso = '2026-09-13'", "DATE", "VARCHAR"),
    ],
)
def test_bind_select_rechaza_comparacion_de_tipo_incompatible(
    condition: str,
    expected_type: str,
    received: str,
) -> None:
    source = f"SELECT * FROM datos WHERE {condition}"
    statement = _select(source)
    assert statement.where is not None

    with pytest.raises(SQLSemanticError) as caught:
        bind_select(statement, _schema(), source)

    assert caught.value.span == statement.where.value.span  # type: ignore[union-attr]
    assert caught.value.source == source
    assert f"se esperaba {expected_type}" in caught.value.message
    assert f"se recibio {received}" in caught.value.message


@pytest.mark.parametrize(
    ("source", "bound_name"),
    [
        ("SELECT * FROM datos WHERE id BETWEEN 'uno' AND 2", "lower"),
        ("SELECT * FROM datos WHERE id BETWEEN 1 AND 'dos'", "upper"),
    ],
)
def test_bind_select_rechaza_cada_limite_between_con_span_preciso(
    source: str,
    bound_name: str,
) -> None:
    statement = _select(source)
    assert statement.where is not None

    with pytest.raises(SQLSemanticError) as caught:
        bind_select(statement, _schema(), source)

    assert caught.value.span == getattr(statement.where, bound_name).span
    assert caught.value.source == source


@pytest.mark.parametrize(
    "condition",
    [
        "id = 2147483648",
        "nombre = 'ñññññ'",
    ],
)
def test_bind_select_reutiliza_limites_de_insert(condition: str) -> None:
    statement = _select(f"SELECT * FROM datos WHERE {condition}")

    with pytest.raises(SQLSemanticError):
        bind_select(statement, _schema())


@pytest.mark.parametrize(
    ("source", "feature"),
    [
        ("SELECT COUNT(*) FROM datos", "funciones de agregado"),
        ("SELECT activo FROM datos GROUP BY activo", "GROUP BY"),
        ("SELECT * FROM datos ORDER BY id", "ORDER BY"),
    ],
)
def test_bind_select_difiere_agregados_group_y_order_al_issue_28(
    source: str,
    feature: str,
) -> None:
    statement = _select(source)

    with pytest.raises(SQLUnsupportedError) as caught:
        bind_select(statement, _schema(), source)

    expected_span = (
        statement.projections[0].span
        if "COUNT" in source
        else statement.group_by.span
        if statement.group_by is not None
        else statement.order_by.span  # type: ignore[union-attr]
    )
    assert caught.value.span == expected_span
    assert caught.value.source == source
    assert feature in caught.value.message
    assert "#28" in caught.value.message


def test_bind_select_rechaza_ast_manual_con_wildcard_y_otra_proyeccion() -> None:
    statement = _select("SELECT id FROM datos")
    wildcard = Wildcard(statement.projections[0].span)
    statement = replace(statement, projections=(wildcard, *statement.projections))

    with pytest.raises(SQLSemanticError) as caught:
        bind_select(statement, _schema())

    assert caught.value.span == wildcard.span
    assert "no se puede combinar" in caught.value.message


def test_bind_select_rechaza_ast_manual_sin_proyecciones() -> None:
    statement = replace(_select("SELECT id FROM datos"), projections=())

    with pytest.raises(SQLSemanticError) as caught:
        bind_select(statement, _schema())

    assert caught.value.span == statement.span
    assert "proyeccion" in caught.value.message
