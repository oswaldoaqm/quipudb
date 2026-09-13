"""Posiciones de texto compartidas por el lexer, el parser y el AST."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Span:
    """Intervalo de una consulta SQL.

    ``start`` y ``end`` son offsets de caracteres desde cero; ``end`` es
    exclusivo. Las lineas y columnas se cuentan desde uno. Un intervalo vacio
    es valido para representar, por ejemplo, el final de la entrada.
    """

    start: int
    end: int
    line: int
    column: int
    end_line: int
    end_column: int

    def __post_init__(self) -> None:
        if self.start < 0:
            raise ValueError("start no puede ser negativo")
        if self.end < self.start:
            raise ValueError("end no puede ser menor que start")
        if self.line < 1 or self.column < 1:
            raise ValueError("la linea y la columna iniciales deben empezar en 1")
        if self.end_line < 1 or self.end_column < 1:
            raise ValueError("la linea y la columna finales deben empezar en 1")
        if self.end_line < self.line:
            raise ValueError("end_line no puede ser anterior a line")
        if self.end_line == self.line and self.end_column < self.column:
            raise ValueError("end_column no puede ser anterior a column en la misma linea")

    def through(self, other: Span) -> Span:
        """Devuelve el menor intervalo que cubre ambos spans."""

        return combine_spans(self, other)


def combine_spans(*spans: Span) -> Span:
    """Devuelve el menor intervalo que cubre todos los spans recibidos."""

    if not spans:
        raise ValueError("se necesita al menos un span")

    first = min(spans, key=lambda span: span.start)
    last = max(spans, key=lambda span: span.end)
    return Span(
        start=first.start,
        end=last.end,
        line=first.line,
        column=first.column,
        end_line=last.end_line,
        end_column=last.end_column,
    )
