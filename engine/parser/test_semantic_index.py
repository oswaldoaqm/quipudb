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


def test_bind_create_index_exige_rtree_para_point() -> None:
    source = "CREATE INDEX por_ubicacion ON lugares (ubicacion) USING BPLUS"
    statement = _create_index(source)
    schema = BoundSchema(
        "lugares",
        (
            BoundColumn("id", SqlTypeName.INT, None),
            BoundColumn("ubicacion", SqlTypeName.POINT, None),
        ),
        0,
    )

    with pytest.raises(SQLSemanticError, match="requiere un indice R-Tree") as caught:
        bind_create_index(statement, schema, source)

    assert caught.value.span == statement.column.span


def test_bind_create_index_admite_rtree_solo_sobre_point() -> None:
    point_source = "CREATE INDEX por_ubicacion ON lugares (ubicacion) USING RTREE"
    point_statement = _create_index(point_source)
    point_schema = BoundSchema(
        "lugares",
        (
            BoundColumn("id", SqlTypeName.INT, None),
            BoundColumn("ubicacion", SqlTypeName.POINT, None),
        ),
        0,
    )

    bound = bind_create_index(point_statement, point_schema, point_source)

    assert bound.kind is IndexKind.RTREE
    scalar_source = "CREATE INDEX por_nombre ON alumnos (nombre) USING RTREE"
    scalar_statement = _create_index(scalar_source)
    with pytest.raises(SQLSemanticError, match="RTREE requiere coordenadas") as caught:
        bind_create_index(scalar_statement, _SCHEMA, scalar_source)
    assert caught.value.span == scalar_statement.column.span


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


# ---------------------------------------------------------------------------
# Indice de texto (#167)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("spelling", ["FULLTEXT", "INVERTED_INDEX"])
def test_bind_create_index_de_texto_sobre_varchar(spelling: str) -> None:
    statement = _create_index(f"CREATE INDEX por_nombre ON alumnos (nombre) USING {spelling}")

    bound = bind_create_index(statement, _SCHEMA)

    assert bound.kind is IndexKind.INVERTED_INDEX
    assert bound.column_name == "nombre"


def test_bind_create_index_de_texto_rechaza_columna_que_no_es_texto() -> None:
    source = "CREATE INDEX por_id ON alumnos (id) USING FULLTEXT"
    statement = _create_index(source)

    with pytest.raises(SQLSemanticError, match="requiere una columna VARCHAR") as caught:
        bind_create_index(statement, _SCHEMA, source)

    # El error apunta a la columna: linea y columna de "id" en el texto.
    assert caught.value.span == statement.column.span
    assert source[caught.value.span.start : caught.value.span.end] == "id"
    assert caught.value.span.line == 1


def test_bind_create_index_de_texto_rechaza_point() -> None:
    source = "CREATE INDEX por_ubicacion ON lugares (ubicacion) USING FULLTEXT"
    statement = _create_index(source)
    schema = BoundSchema(
        "lugares",
        (
            BoundColumn("id", SqlTypeName.INT, None),
            BoundColumn("ubicacion", SqlTypeName.POINT, None),
        ),
        0,
    )

    with pytest.raises(SQLSemanticError, match="requiere una columna VARCHAR") as caught:
        bind_create_index(statement, schema, source)

    assert caught.value.span == statement.column.span
