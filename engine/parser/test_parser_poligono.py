"""Pruebas del parser para DENTRO(columna, POLYGON(...))."""

import pytest

from engine.parser import DeleteStatement, SelectStatement, parse_sql
from engine.parser.ast import PolygonCondition
from engine.parser.errors import SQLParseError

_L = (
    "POLYGON(POINT(-12.20, -77.20), POINT(-12.20, -76.90), POINT(-12.10, -76.90), "
    "POINT(-12.10, -77.05), POINT(-11.90, -77.05), POINT(-11.90, -77.20))"
)


def test_dentro_parsea_columna_y_vertices_en_orden() -> None:
    sql = f"SELECT * FROM tiendas WHERE dentro(ubicacion, {_L})"

    statement = parse_sql(sql)

    assert isinstance(statement, SelectStatement)
    condition = statement.where
    assert isinstance(condition, PolygonCondition)
    assert condition.column.name.name == "ubicacion"
    assert [(v.latitude, v.longitude) for v in condition.polygon.vertices] == [
        (-12.20, -77.20),
        (-12.20, -76.90),
        (-12.10, -76.90),
        (-12.10, -77.05),
        (-11.90, -77.05),
        (-11.90, -77.20),
    ]
    assert sql[condition.span.start : condition.span.end] == f"dentro(ubicacion, {_L})"
    assert sql[condition.polygon.span.start : condition.polygon.span.end] == _L


def test_dentro_tambien_sirve_en_delete() -> None:
    statement = parse_sql(f"DELETE FROM tiendas WHERE DENTRO(ubicacion, {_L})")

    assert isinstance(statement, DeleteStatement)
    assert isinstance(statement.where, PolygonCondition)


@pytest.mark.parametrize(
    ("where", "message"),
    [
        ("DENTRO(ubicacion, POLYGON(POINT(0, 0), POINT(1, 1)))", "al menos 3 vertices y tiene 2"),
        ("DENTRO(ubicacion, POINT(0, 0))", "se esperaba POLYGON"),
        ("DENTRO(ubicacion, POLYGON(POINT(0, 0), 1, POINT(1, 1)))", "debe ser un POINT"),
        ("DENTRO(ubicacion, POLYGON(POINT(95, 0), POINT(0, 1), POINT(1, 1)))", "latitud"),
        ("DENTRO(POLYGON(POINT(0, 0), POINT(0, 1), POINT(1, 1)))", "se esperaba una columna"),
        ("DENTRO(ubicacion, POLYGON(POINT(0, 0), POINT(0, 1), POINT(1, 1))", "despues de DENTRO"),
    ],
)
def test_dentro_rechaza_formas_invalidas(where: str, message: str) -> None:
    with pytest.raises(SQLParseError, match=message):
        parse_sql(f"SELECT * FROM tiendas WHERE {where}")
