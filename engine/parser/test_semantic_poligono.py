"""Enlace semantico de DENTRO(columna, POLYGON(...))."""

import pytest

from engine.parser import SelectStatement, parse_sql
from engine.parser.ast import SqlTypeName
from engine.parser.bound_ast import (
    BoundColumn,
    BoundPolygonCondition,
    BoundSchema,
    PointValue,
)
from engine.parser.errors import SQLSemanticError
from engine.parser.semantic import bind_select

_SCHEMA = BoundSchema(
    "tiendas",
    (
        BoundColumn("id", SqlTypeName.INT, None),
        BoundColumn("ubicacion", SqlTypeName.POINT, None),
    ),
    0,
)
_TRIANGULO = "POLYGON(POINT(-12.2, -77.2), POINT(-12.2, -76.9), POINT(-11.9, -77.0))"


def _bind(source: str):
    statement = parse_sql(source)
    assert isinstance(statement, SelectStatement)
    return bind_select(statement, _SCHEMA, source)


def test_dentro_resuelve_la_columna_point_y_los_vertices() -> None:
    bound = _bind(f"SELECT id FROM tiendas WHERE DENTRO(ubicacion, {_TRIANGULO})")

    assert isinstance(bound.where, BoundPolygonCondition)
    assert bound.where.column.index == 1
    assert bound.where.vertices == (
        PointValue(-12.2, -77.2),
        PointValue(-12.2, -76.9),
        PointValue(-11.9, -77.0),
    )


@pytest.mark.parametrize(
    ("source", "message", "fragment"),
    [
        (
            f"SELECT * FROM tiendas WHERE DENTRO(id, {_TRIANGULO})",
            "DENTRO requiere una columna POINT",
            "id",
        ),
        (
            (
                "SELECT * FROM tiendas WHERE DENTRO(ubicacion, "
                "POLYGON(POINT(1, 1), POINT(1, 1), POINT(2, 2)))"
            ),
            "al menos 3 vertices distintos",
            "POLYGON(POINT(1, 1), POINT(1, 1), POINT(2, 2))",
        ),
    ],
)
def test_dentro_rechaza_columnas_y_poligonos_invalidos(
    source: str, message: str, fragment: str
) -> None:
    with pytest.raises(SQLSemanticError, match=message) as caught:
        _bind(source)

    assert source[caught.value.span.start : caught.value.span.end] == fragment
