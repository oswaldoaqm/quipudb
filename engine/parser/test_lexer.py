"""Pruebas del lexer manual del subconjunto SQL."""

from dataclasses import FrozenInstanceError

import pytest

from engine.parser.errors import SQLLexError, SQLUnsupportedError
from engine.parser.lexer import tokenize
from engine.parser.span import Span
from engine.parser.tokens import TokenKind

KEYWORDS = (
    ("create", TokenKind.CREATE),
    ("table", TokenKind.TABLE),
    ("primary", TokenKind.PRIMARY),
    ("key", TokenKind.KEY),
    ("using", TokenKind.USING),
    ("heap", TokenKind.HEAP),
    ("sequential", TokenKind.SEQUENTIAL),
    ("insert", TokenKind.INSERT),
    ("into", TokenKind.INTO),
    ("values", TokenKind.VALUES),
    ("select", TokenKind.SELECT),
    ("from", TokenKind.FROM),
    ("where", TokenKind.WHERE),
    ("delete", TokenKind.DELETE),
    ("order", TokenKind.ORDER),
    ("by", TokenKind.BY),
    ("asc", TokenKind.ASC),
    ("desc", TokenKind.DESC),
    ("group", TokenKind.GROUP),
    ("count", TokenKind.COUNT),
    ("sum", TokenKind.SUM),
    ("min", TokenKind.MIN),
    ("max", TokenKind.MAX),
    ("avg", TokenKind.AVG),
    ("between", TokenKind.BETWEEN),
    ("and", TokenKind.AND),
    ("int", TokenKind.INT),
    ("double", TokenKind.DOUBLE),
    ("varchar", TokenKind.VARCHAR),
    ("bool", TokenKind.BOOL),
    ("date", TokenKind.DATE),
    ("true", TokenKind.TRUE),
    ("false", TokenKind.FALSE),
)


@pytest.mark.parametrize(("lexeme", "kind"), KEYWORDS)
def test_keywords_no_distinguen_mayusculas(lexeme: str, kind: TokenKind) -> None:
    mixed_case = "".join(
        character.upper() if index % 2 == 0 else character for index, character in enumerate(lexeme)
    )

    token, eof = tokenize(mixed_case)

    assert token.kind is kind
    assert token.lexeme == mixed_case
    expected_value = True if kind is TokenKind.TRUE else False if kind is TokenKind.FALSE else None
    assert token.value is expected_value
    assert token.span == Span(0, len(lexeme), 1, 1, 1, len(lexeme) + 1)
    assert eof.kind is TokenKind.EOF


def test_identificadores_preservan_casing_y_no_confunden_prefijos() -> None:
    tokens = tokenize("Alumno_2026 selectivo _FROM año")

    assert [token.kind for token in tokens] == [
        TokenKind.IDENTIFIER,
        TokenKind.IDENTIFIER,
        TokenKind.IDENTIFIER,
        TokenKind.IDENTIFIER,
        TokenKind.EOF,
    ]
    assert [token.value for token in tokens[:-1]] == [
        "Alumno_2026",
        "selectivo",
        "_FROM",
        "año",
    ]


@pytest.mark.parametrize("source", ["ſelect", "ıNSERT"])
def test_confundibles_unicode_no_se_convierten_en_keywords(source: str) -> None:
    token, _ = tokenize(source)

    assert token.kind is TokenKind.IDENTIFIER
    assert token.value == source


def test_identificador_solo_admite_digitos_ascii_despues_de_letra() -> None:
    with pytest.raises(SQLLexError, match="caracter inesperado '²'") as raised:
        tokenize("codigo²")

    assert raised.value.column == 7


@pytest.mark.parametrize(
    ("source", "kind"),
    [
        ("(", TokenKind.LPAREN),
        (")", TokenKind.RPAREN),
        (",", TokenKind.COMMA),
        (";", TokenKind.SEMICOLON),
        ("*", TokenKind.STAR),
        ("=", TokenKind.EQUAL),
        ("<", TokenKind.LESS_THAN),
        ("<=", TokenKind.LESS_THAN_OR_EQUAL),
        (">", TokenKind.GREATER_THAN),
        (">=", TokenKind.GREATER_THAN_OR_EQUAL),
    ],
)
def test_puntuacion_y_operadores(source: str, kind: TokenKind) -> None:
    token, eof = tokenize(source)

    assert token.kind is kind
    assert token.lexeme == source
    assert token.value is None
    assert token.span == Span(0, len(source), 1, 1, 1, len(source) + 1)
    assert eof.span.start == len(source)


def test_enteros_y_doubles_con_signo_adyacente() -> None:
    tokens = tokenize("0 12 -2 1.5 -0.25")

    assert [token.kind for token in tokens] == [
        TokenKind.INTEGER,
        TokenKind.INTEGER,
        TokenKind.INTEGER,
        TokenKind.DOUBLE_LITERAL,
        TokenKind.DOUBLE_LITERAL,
        TokenKind.EOF,
    ]
    assert [token.lexeme for token in tokens[:-1]] == ["0", "12", "-2", "1.5", "-0.25"]
    assert [token.value for token in tokens[:-1]] == [0, 12, -2, 1.5, -0.25]


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ("9" * 5000, "literal entero fuera de rango"),
        ("9" * 500 + ".0", "literal double fuera de rango"),
        ("0." + "0" * 400 + "1", "literal double fuera de rango"),
    ],
)
def test_numeros_fuera_de_rango_siguen_el_contrato_de_error(
    source: str,
    message: str,
) -> None:
    with pytest.raises(SQLLexError, match=message) as raised:
        tokenize(source)

    assert raised.value.span == Span(0, len(source), 1, 1, 1, len(source) + 1)


def test_strings_con_escape_sql_vacio_y_salto_de_linea() -> None:
    tokens = tokenize("'O''Brien' '' 'dos\nlineas'")

    assert [token.kind for token in tokens] == [
        TokenKind.STRING,
        TokenKind.STRING,
        TokenKind.STRING,
        TokenKind.EOF,
    ]
    assert [token.value for token in tokens[:-1]] == ["O'Brien", "", "dos\nlineas"]
    assert tokens[0].lexeme == "'O''Brien'"
    assert tokens[2].span == Span(14, 26, 1, 15, 2, 8)
    assert tokens[-1].span == Span(26, 26, 2, 8, 2, 8)


def test_spans_con_lf_crlf_y_espacios() -> None:
    tokens = tokenize("SELECT\n  nombre\r\nFROM")

    assert tokens[0].span == Span(0, 6, 1, 1, 1, 7)
    assert tokens[1].span == Span(9, 15, 2, 3, 2, 9)
    assert tokens[2].span == Span(17, 21, 3, 1, 3, 5)
    assert tokens[3].span == Span(21, 21, 3, 5, 3, 5)


def test_token_es_inmutable() -> None:
    token = tokenize("codigo")[0]

    with pytest.raises(FrozenInstanceError):
        token.value = "otro"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("source", "offset", "column", "message"),
    [
        (".5", 0, 1, "caracter inesperado '.'"),
        ("-", 0, 1, "caracter inesperado '-'"),
        ("- 2", 0, 1, "caracter inesperado '-'"),
        ("1.", 0, 1, "literal numerico incompleto"),
        ("SELECT @", 7, 8, "caracter inesperado '@'"),
        ("SELECT\u2028*", 6, 7, "caracter inesperado '\\u2028'"),
    ],
)
def test_entrada_invalida_informa_posicion(
    source: str,
    offset: int,
    column: int,
    message: str,
) -> None:
    with pytest.raises(SQLLexError) as raised:
        tokenize(source)

    error = raised.value
    assert error.message == message
    assert error.offset == offset
    assert error.line == 1
    assert error.column == column
    assert error.source == source
    assert "^" in str(error)


@pytest.mark.parametrize("operator", ["!=", "<>"])
def test_operadores_fuera_del_subconjunto_son_explicitos(operator: str) -> None:
    with pytest.raises(SQLUnsupportedError, match="no soportado") as raised:
        tokenize(operator)

    assert raised.value.span == Span(0, 2, 1, 1, 1, 3)


def test_caracter_invalido_despues_de_salto_informa_linea() -> None:
    source = "SELECT\n  @"

    with pytest.raises(SQLLexError) as raised:
        tokenize(source)

    assert raised.value.span == Span(9, 10, 2, 3, 2, 4)
    assert "linea 2, columna 3, offset 9" in str(raised.value)


@pytest.mark.parametrize(
    ("source", "expected_span"),
    [
        ("'sin cerrar", Span(0, 11, 1, 1, 1, 12)),
        ("SELECT 'dos\nlineas", Span(7, 18, 1, 8, 2, 7)),
    ],
)
def test_string_sin_cerrar_cubre_hasta_eof(source: str, expected_span: Span) -> None:
    with pytest.raises(SQLLexError, match="string sin cerrar") as raised:
        tokenize(source)

    assert raised.value.span == expected_span
    assert raised.value.source == source


def test_entrada_vacia_solo_produce_eof() -> None:
    eof = tokenize("")[0]
    assert eof.kind is TokenKind.EOF
    assert eof.lexeme == ""
    assert eof.value is None
    assert eof.span == Span(0, 0, 1, 1, 1, 1)

    whitespace_eof = tokenize("   ")[0]
    assert whitespace_eof.kind is TokenKind.EOF
    assert whitespace_eof.span == Span(3, 3, 1, 4, 1, 4)
