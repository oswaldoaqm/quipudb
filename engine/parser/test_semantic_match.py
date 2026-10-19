"""Enlace semantico de MATCH (#168)."""

import pytest

from engine.parser import DeleteStatement, SelectStatement, TextRanking, parse_sql
from engine.parser.ast import SqlTypeName
from engine.parser.bound_ast import BoundColumn, BoundMatchCondition, BoundSchema
from engine.parser.errors import SQLSemanticError, SQLUnsupportedError
from engine.parser.semantic import bind_delete, bind_select

_DOCUMENTOS = BoundSchema(
    "documentos",
    (
        BoundColumn("id", SqlTypeName.INT, None),
        BoundColumn("contenido", SqlTypeName.VARCHAR, 200),
        BoundColumn("ubicacion", SqlTypeName.POINT, None),
    ),
    0,
)
_AUTORES = BoundSchema(
    "autores",
    (
        BoundColumn("id", SqlTypeName.INT, None),
        BoundColumn("nombre", SqlTypeName.VARCHAR, 40),
    ),
    0,
)


def _bind(source: str):
    statement = parse_sql(source)
    assert isinstance(statement, SelectStatement)
    return bind_select(statement, {"documentos": _DOCUMENTOS, "autores": _AUTORES}, source)


def test_match_resuelve_la_columna_la_consulta_y_el_ranking() -> None:
    bound = _bind(
        "SELECT * FROM documentos WHERE MATCH(contenido, 'base de datos vectorial') "
        "USING TF_IDF LIMIT 10"
    )

    assert isinstance(bound.where, BoundMatchCondition)
    assert bound.where.column.index == 1
    assert bound.where.column.column.name == "contenido"
    assert bound.where.query == "base de datos vectorial"
    assert bound.where.ranking is TextRanking.TF_IDF
    assert bound.where.ranking_explicit is True
    assert bound.limit == 10


def test_sin_using_queda_bm25_y_se_sabe_que_no_se_eligio() -> None:
    bound = _bind("SELECT id FROM documentos WHERE MATCH(contenido, 'oro')")

    assert isinstance(bound.where, BoundMatchCondition)
    assert bound.where.ranking is TextRanking.BM25
    assert bound.where.ranking_explicit is False


@pytest.mark.parametrize(("column", "tipo"), [("id", "INT"), ("ubicacion", "POINT")])
def test_la_columna_tiene_que_ser_texto_y_el_error_la_ubica(column: str, tipo: str) -> None:
    source = f"SELECT * FROM documentos WHERE MATCH({column}, 'oro')"

    with pytest.raises(SQLSemanticError, match=f"MATCH requiere una columna VARCHAR.*{tipo}") as e:
        _bind(source)

    assert source[e.value.span.start : e.value.span.end] == column
    assert e.value.span.line == 1


def test_una_columna_que_no_existe_es_error_ubicado() -> None:
    source = "SELECT * FROM documentos WHERE MATCH(titulo, 'oro')"

    with pytest.raises(SQLSemanticError, match="titulo") as caught:
        _bind(source)

    assert source[caught.value.span.start : caught.value.span.end] == "titulo"


def test_match_con_join_no_esta_soportado() -> None:
    source = (
        "SELECT * FROM documentos JOIN autores ON documentos.id = autores.id "
        "WHERE MATCH(contenido, 'oro')"
    )

    with pytest.raises(SQLUnsupportedError, match="JOIN") as caught:
        _bind(source)

    assert source[caught.value.span.start : caught.value.span.end] == "MATCH(contenido, 'oro')"


def test_match_en_delete_no_esta_soportado() -> None:
    source = "DELETE FROM documentos WHERE MATCH(contenido, 'oro')"
    statement = parse_sql(source)
    assert isinstance(statement, DeleteStatement)

    with pytest.raises(SQLUnsupportedError, match="solo se admite en SELECT") as caught:
        bind_delete(statement, _DOCUMENTOS, source)

    assert source[caught.value.span.start : caught.value.span.end] == "MATCH(contenido, 'oro')"
