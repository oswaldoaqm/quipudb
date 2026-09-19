"""Pruebas del enlace semantico puro de CREATE INDEX."""

from dataclasses import FrozenInstanceError

import pytest

from engine.parser import CreateIndexStatement, IndexKind, SqlTypeName, parse_sql
from engine.parser.bound_ast import BoundColumn, BoundCreateIndexStatement, BoundSchema
from engine.parser.errors import SQLSemanticError
from engine.parser.semantic import bind_create_index

_SCHEMA = BoundSchema(
    "alumnos",
    (
        BoundColumn("id", SqlTypeName.INT, None),
        BoundColumn("nombre", SqlTypeName.VARCHAR, 20),
    ),
    0,
)


def _create_index(source: str) -> CreateIndexStatement:
    statement = parse_sql(source)
    assert isinstance(statement, CreateIndexStatement)
    return statement


def test_bind_create_index_resuelve_nombres_y_estructura() -> None:
    statement = _create_index("CREATE INDEX por_nombre ON alumnos (nombre) USING HASH")

    bound = bind_create_index(statement, _SCHEMA)

    assert isinstance(bound, BoundCreateIndexStatement)
    assert bound.index_name == "por_nombre"
    assert bound.table_name == "alumnos"
    assert bound.column_name == "nombre"
    assert bound.kind is IndexKind.EXTENDIBLE_HASH
    with pytest.raises(FrozenInstanceError):
        bound.index_name = "otro"  # type: ignore[misc]


def test_bind_create_index_rechaza_tabla_que_no_corresponde_al_esquema() -> None:
    source = "CREATE INDEX por_nombre ON cursos (nombre) USING HASH"
    statement = _create_index(source)

    with pytest.raises(SQLSemanticError, match="el esquema es de alumnos") as caught:
        bind_create_index(statement, _SCHEMA, source)

    assert caught.value.span == statement.table.span
    assert caught.value.source == source


def test_bind_create_index_rechaza_columna_inexistente() -> None:
    source = "CREATE INDEX por_nota ON alumnos (nota) USING BPLUS"
    statement = _create_index(source)

    with pytest.raises(SQLSemanticError, match="columna 'nota' no existe") as caught:
        bind_create_index(statement, _SCHEMA, source)

    assert caught.value.span == statement.column.span


@pytest.mark.parametrize(
    "source",
    [
        "CREATE INDEX año ON alumnos (nombre) USING HASH",
        "CREATE INDEX por_nombre ON alumnós (nombre) USING HASH",
        "CREATE INDEX por_nombre ON alumnos (nómbre) USING HASH",
    ],
)
def test_bind_create_index_valida_los_tres_identificadores(source: str) -> None:
    statement = _create_index(source)

    with pytest.raises(SQLSemanticError, match="nombre de (indice|tabla|columna) invalido"):
        bind_create_index(statement, _SCHEMA, source)
