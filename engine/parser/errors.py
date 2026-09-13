"""Errores con ubicacion precisa para el procesamiento de SQL."""

from __future__ import annotations

import re

from engine.parser.span import Span


_LINE_BREAK = re.compile(r"\r\n|\r|\n")


class SQLError(Exception):
    """Error de SQL asociado a una posicion de la consulta original."""

    def __init__(self, message: str, span: Span, source: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.span = span
        self.source = source

    @property
    def offset(self) -> int:
        return self.span.start

    @property
    def line(self) -> int:
        return self.span.line

    @property
    def column(self) -> int:
        return self.span.column

    def __str__(self) -> str:
        location = f"linea {self.line}, columna {self.column}, offset {self.offset}"
        summary = f"{self.message} ({location})"
        fragment = self._source_fragment()
        if fragment is None:
            return summary
        line, marker = fragment
        return f"{summary}\n{line}\n{marker}"

    def _source_fragment(self) -> tuple[str, str] | None:
        if self.source is None:
            return None

        lines = _LINE_BREAK.split(self.source)
        if self.line > len(lines):
            return None

        source_line = lines[self.line - 1]
        marker_width = 1
        if self.span.end_line == self.line:
            marker_width = max(1, self.span.end_column - self.column)
        prefix = source_line[: self.column - 1]
        marker_prefix = "".join("\t" if character == "\t" else " " for character in prefix)
        marker = marker_prefix + "^" * marker_width
        return source_line, marker


class SQLLexError(SQLError):
    """La entrada contiene un caracter o literal que el lexer no reconoce."""


class SQLParseError(SQLError):
    """La secuencia de tokens no cumple la gramatica SQL soportada."""


class SQLSemanticError(SQLError):
    """La sentencia es sintactica, pero no es valida para el esquema."""


class SQLUnsupportedError(SQLError):
    """La sentencia solicita una caracteristica fuera del alcance soportado."""
