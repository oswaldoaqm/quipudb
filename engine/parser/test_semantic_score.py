"""SCORE() y ORDER BY sobre el puntaje de MATCH (#169): sintaxis y semantica."""

import pytest

from engine.parser import (
    OrderBy,
    ScoreCall,
    ScoreOrderBy,
    SelectStatement,
    parse_sql,
)
from engine.parser.ast import OrderDirection, SqlTypeName
from engine.parser.bound_ast import BoundColumn, BoundSchema
from engine.parser.errors import SQLParseError, SQLSemanticError, SQLUnsupportedError
from engine.parser.semantic import bind_select

_ARTICULOS = BoundSchema(
    "articulos",
    (
        BoundColumn("id", SqlTypeName.INT, None),
        BoundColumn("autor", SqlTypeName.VARCHAR, 40),
        BoundColumn("texto", SqlTypeName.VARCHAR, 200),
    ),
    0,
)
_ENUNCIADO = (
    "SELECT *, SCORE() as relevancia FROM articulos "
    "WHERE MATCH(texto, 'machine learning') USING BM25 ORDER BY relevancia DESC"
)


def _parse(source: str) -> SelectStatement:
    statement = parse_sql(source)
    assert isinstance(statement, SelectStatement)
    return statement


def _bind(source: str):
    return bind_select(_parse(source), _ARTICULOS, source)


# --- sintaxis -----------------------------------------------------------------


def test_la_consulta_del_enunciado_se_parsea() -> None:
    statement = _parse(_ENUNCIADO)

    score = statement.projections[1]
    assert isinstance(score, ScoreCall)
    assert score.alias is not None and score.alias.name == "relevancia"
    assert _ENUNCIADO[score.span.start : score.span.end] == "SCORE() as relevancia"
    assert isinstance(statement.order_by, OrderBy)
    assert statement.order_by.direction is OrderDirection.DESC


def test_score_sin_alias_y_en_cualquier_posicion() -> None:
    statement = _parse("SELECT SCORE(), id FROM articulos WHERE MATCH(texto, 'x')")

    assert isinstance(statement.projections[0], ScoreCall)
    assert statement.projections[0].alias is None


@pytest.mark.parametrize(
    ("sql", "direction"),
    [
        ("ORDER BY SCORE() DESC", OrderDirection.DESC),
        ("ORDER BY SCORE() ASC", OrderDirection.ASC),
        ("ORDER BY SCORE()", OrderDirection.ASC),
    ],
)
def test_order_by_score(sql: str, direction: OrderDirection) -> None:
    statement = _parse(f"SELECT id FROM articulos WHERE MATCH(texto, 'x') {sql}")

    assert isinstance(statement.order_by, ScoreOrderBy)
    assert statement.order_by.direction is direction


@pytest.mark.parametrize(
    ("sql", "message"),
    [
        ("SELECT *, id FROM articulos", "salvo SCORE"),
        ("SELECT SCORE(texto) FROM articulos", "SCORE\\(\\) no recibe argumentos"),
        ("SELECT SCORE FROM articulos", "se esperaba '\\(' despues de SCORE"),
        ("SELECT SCORE() AS FROM articulos", "se esperaba un nombre despues de AS"),
        ("SELECT id FROM articulos ORDER BY SCORE(x)", "SCORE\\(\\) no recibe argumentos"),
    ],
)
def test_errores_de_sintaxis(sql: str, message: str) -> None:
    with pytest.raises(SQLParseError, match=message):
        parse_sql(sql)


# --- semantica ----------------------------------------------------------------


def test_score_va_despues_de_todas_las_columnas_de_estrella() -> None:
    bound = _bind(_ENUNCIADO)

    assert bound.wildcard is True
    assert bound.score is not None
    assert bound.score.name == "relevancia"
    assert bound.score.position == 3
    # ORDER BY relevancia es el puntaje, no una columna: no pasa por order_by.
    assert bound.order_by is None
    assert bound.score_order is not None
    assert bound.score_order.direction is OrderDirection.DESC


def test_score_entre_columnas_conserva_su_lugar_y_sin_alias_se_llama_score() -> None:
    bound = _bind("SELECT id, SCORE(), autor FROM articulos WHERE MATCH(texto, 'x')")

    assert [p.column.name for p in bound.projections] == ["id", "autor"]
    assert bound.score is not None
    assert (bound.score.name, bound.score.position) == ("score", 1)
    assert bound.score_order is None


def test_order_by_score_sin_proyectarlo() -> None:
    bound = _bind("SELECT id FROM articulos WHERE MATCH(texto, 'x') ORDER BY SCORE() ASC")

    assert bound.score is None
    assert bound.score_order is not None
    assert bound.score_order.direction is OrderDirection.ASC


def test_el_alias_gana_a_una_columna_con_el_mismo_nombre() -> None:
    bound = _bind(
        "SELECT SCORE() AS autor FROM articulos WHERE MATCH(texto, 'x') ORDER BY autor DESC"
    )

    assert bound.order_by is None
    assert bound.score_order is not None


def test_ordenar_por_otra_columna_sin_score_sigue_igual() -> None:
    bound = _bind("SELECT id FROM articulos WHERE MATCH(texto, 'x') ORDER BY autor")

    assert bound.order_by is not None
    assert bound.score_order is None


@pytest.mark.parametrize(
    ("source", "fragment"),
    [
        ("SELECT id, SCORE() FROM articulos", "SCORE()"),
        ("SELECT SCORE() AS s FROM articulos WHERE id = 3", "SCORE() AS s"),
        ("SELECT id FROM articulos ORDER BY SCORE() DESC", "ORDER BY SCORE() DESC"),
    ],
)
def test_score_sin_match_es_error_ubicado(source: str, fragment: str) -> None:
    with pytest.raises(SQLSemanticError, match="necesita MATCH") as caught:
        _bind(source)

    assert source[caught.value.span.start : caught.value.span.end] == fragment


def test_dos_score_es_error_en_el_segundo() -> None:
    source = "SELECT SCORE(), SCORE() FROM articulos WHERE MATCH(texto, 'x')"

    with pytest.raises(SQLSemanticError, match="una vez") as caught:
        _bind(source)

    assert caught.value.span.start == source.rindex("SCORE()")


def test_score_con_group_by_no_esta_soportado() -> None:
    source = (
        "SELECT autor, COUNT(*), SCORE() FROM articulos "
        "WHERE MATCH(texto, 'x') GROUP BY autor"
    )

    with pytest.raises(SQLUnsupportedError, match="GROUP BY") as caught:
        _bind(source)

    assert source[caught.value.span.start : caught.value.span.end] == "SCORE()"


def test_score_con_order_by_de_otra_columna_no_esta_soportado() -> None:
    source = "SELECT id, SCORE() FROM articulos WHERE MATCH(texto, 'x') ORDER BY autor"

    with pytest.raises(SQLUnsupportedError, match="External") as caught:
        _bind(source)

    assert source[caught.value.span.start : caught.value.span.end] == "ORDER BY autor"
