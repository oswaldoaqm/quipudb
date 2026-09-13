"""Pruebas de errores SQL con ubicacion y contexto."""

import pytest

from engine.parser.errors import (
    SQLError,
    SQLLexError,
    SQLParseError,
    SQLSemanticError,
    SQLUnsupportedError,
)
from engine.parser.span import Span


@pytest.mark.parametrize(
    "error_type",
    [SQLLexError, SQLParseError, SQLSemanticError, SQLUnsupportedError],
)
def test_errores_especificos_heredan_posicion(error_type: type[SQLError]) -> None:
    error = error_type("mensaje", Span(8, 9, 2, 3, 2, 4))

    assert isinstance(error, SQLError)
    assert error.args == ("mensaje",)
    assert error.offset == 8
    assert error.line == 2
    assert error.column == 3
    assert str(error) == "mensaje (linea 2, columna 3, offset 8)"


def test_error_multilinea_muestra_fragmento_y_caret() -> None:
    source = "SELECT *\nFROM alumnos\nWHERE codigo = ?"
    error = SQLLexError("caracter inesperado '?'", Span(37, 38, 3, 16, 3, 17), source)

    rendered = str(error)
    assert "linea 3, columna 16, offset 37" in rendered
    assert "WHERE codigo = ?" in rendered
    assert rendered.splitlines()[-1] == "               ^"


def test_error_en_eof_despues_de_salto_de_linea_muestra_caret() -> None:
    source = "SELECT *\n"
    error = SQLParseError("se esperaba FROM", Span(9, 9, 2, 1, 2, 1), source)

    assert str(error).splitlines()[-2:] == ["", "^"]


def test_marcador_preserva_tabs_para_alinearse_con_la_fuente() -> None:
    error = SQLLexError("invalido", Span(1, 2, 1, 2, 1, 3), "\t@")

    assert str(error).splitlines()[-2:] == ["\t@", "\t^"]
