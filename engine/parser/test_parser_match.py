"""Sintaxis de MATCH(columna, 'consulta') [USING TF_IDF | BM25] (#168)."""

import pytest

from engine.parser import MatchCondition, SelectStatement, TextRanking, parse_sql
from engine.parser.errors import SQLLexError, SQLParseError


def _where(source: str) -> MatchCondition:
    statement = parse_sql(source)
    assert isinstance(statement, SelectStatement)
    assert isinstance(statement.where, MatchCondition)
    return statement.where


def test_la_consulta_del_enunciado() -> None:
    sql = (
        "SELECT * FROM documentos WHERE MATCH(contenido, 'base de datos vectorial') "
        "USING TF_IDF LIMIT 10"
    )
    statement = parse_sql(sql)

    assert isinstance(statement, SelectStatement)
    match = statement.where
    assert isinstance(match, MatchCondition)
    assert match.column.name.name == "contenido"
    assert match.query.value == "base de datos vectorial"
    assert match.ranking is TextRanking.TF_IDF
    assert match.ranking_explicit is True
    # El span cubre de MATCH hasta el ranking, que es lo que se cita en el plan.
    assert sql[match.span.start : match.span.end] == (
        "MATCH(contenido, 'base de datos vectorial') USING TF_IDF"
    )
    assert statement.limit is not None and statement.limit.value == 10


def test_using_bm25_elige_el_otro_ranking() -> None:
    match = _where("SELECT * FROM a WHERE MATCH(texto, 'machine learning') USING BM25")

    assert match.ranking is TextRanking.BM25
    assert match.ranking_explicit is True


def test_sin_using_el_ranking_es_bm25() -> None:
    sql = "SELECT * FROM a WHERE MATCH(texto, 'machine learning')"
    match = _where(sql)

    assert match.ranking is TextRanking.BM25
    assert match.ranking_explicit is False
    assert sql[match.span.start : match.span.end] == "MATCH(texto, 'machine learning')"


@pytest.mark.parametrize("spelling", ["tf_idf", "Tf_Idf", "bm25"])
def test_el_ranking_no_distingue_mayusculas(spelling: str) -> None:
    match = _where(f"select * from a where match(texto, 'x') using {spelling}")

    assert match.ranking is TextRanking(spelling.upper())


def test_la_columna_puede_ir_calificada() -> None:
    match = _where("SELECT * FROM a WHERE MATCH(a.texto, 'x')")

    assert match.column.qualifier is not None
    assert match.column.qualifier.name == "a"


def test_un_ranking_desconocido_es_error_con_linea_y_columna() -> None:
    sql = "SELECT * FROM a\nWHERE MATCH(texto, 'x') USING COSENO"

    with pytest.raises(SQLParseError, match="se esperaba TF_IDF o BM25") as caught:
        parse_sql(sql)

    assert (caught.value.span.line, caught.value.span.column) == (2, 31)
    assert sql[caught.value.span.start : caught.value.span.end] == "COSENO"


@pytest.mark.parametrize(
    ("sql", "message"),
    [
        ("SELECT * FROM a WHERE MATCH texto, 'x')", "se esperaba '\\(' despues de MATCH"),
        ("SELECT * FROM a WHERE MATCH('x', texto)", "se esperaba una columna en MATCH"),
        ("SELECT * FROM a WHERE MATCH(texto 'x')", "se esperaba ','"),
        ("SELECT * FROM a WHERE MATCH(texto, 42)", "consulta como texto entre comillas"),
        ("SELECT * FROM a WHERE MATCH(texto, otra)", "consulta como texto entre comillas"),
        ("SELECT * FROM a WHERE MATCH(texto, 'x'", "se esperaba '\\)'"),
        ("SELECT * FROM a WHERE MATCH(texto, 'x') USING", "se esperaba TF_IDF o BM25"),
    ],
)
def test_errores_de_sintaxis(sql: str, message: str) -> None:
    with pytest.raises((SQLParseError, SQLLexError), match=message):
        parse_sql(sql)
