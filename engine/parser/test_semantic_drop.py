"""Pruebas del enlace semantico puro de DROP TABLE."""

from dataclasses import FrozenInstanceError

import pytest

from engine.parser import DropTableStatement, parse_sql
from engine.parser.bound_ast import BoundDropTableStatement
from engine.parser.errors import SQLSemanticError
from engine.parser.semantic import bind_drop_table


def _drop(source: str) -> DropTableStatement:
    statement = parse_sql(source)
    assert isinstance(statement, DropTableStatement)
    return statement


def test_bind_drop_table_valida_y_conserva_nombre_y_span() -> None:
    statement = _drop("DROP TABLE alumnos")

    bound = bind_drop_table(statement)

    assert isinstance(bound, BoundDropTableStatement)
    assert bound.table_name == "alumnos"
    assert bound.span == statement.span
    with pytest.raises(FrozenInstanceError):
        bound.table_name = "otra"  # type: ignore[misc]


def test_bind_drop_table_rechaza_identificador_fuera_del_contrato() -> None:
    source = "DROP TABLE año"
    statement = _drop(source)

    with pytest.raises(SQLSemanticError, match="nombre de tabla invalido") as caught:
        bind_drop_table(statement, source)

    assert caught.value.span == statement.table.span
    assert caught.value.source == source
