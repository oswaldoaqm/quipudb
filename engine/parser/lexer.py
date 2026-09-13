"""Lexer manual para el subconjunto SQL de QuipuDB."""

from __future__ import annotations

import math

from engine.parser.errors import SQLLexError, SQLUnsupportedError
from engine.parser.span import Span
from engine.parser.tokens import Token, TokenKind, TokenValue

_KEYWORDS = {
    kind.value: kind
    for kind in (
        TokenKind.CREATE,
        TokenKind.TABLE,
        TokenKind.PRIMARY,
        TokenKind.KEY,
        TokenKind.USING,
        TokenKind.HEAP,
        TokenKind.SEQUENTIAL,
        TokenKind.INSERT,
        TokenKind.INTO,
        TokenKind.VALUES,
        TokenKind.SELECT,
        TokenKind.FROM,
        TokenKind.WHERE,
        TokenKind.DELETE,
        TokenKind.ORDER,
        TokenKind.BY,
        TokenKind.ASC,
        TokenKind.DESC,
        TokenKind.GROUP,
        TokenKind.COUNT,
        TokenKind.SUM,
        TokenKind.MIN,
        TokenKind.MAX,
        TokenKind.AVG,
        TokenKind.BETWEEN,
        TokenKind.AND,
        TokenKind.INT,
        TokenKind.DOUBLE,
        TokenKind.VARCHAR,
        TokenKind.BOOL,
        TokenKind.DATE,
        TokenKind.TRUE,
        TokenKind.FALSE,
    )
}

_SINGLE_CHARACTER_TOKENS = {
    "(": TokenKind.LPAREN,
    ")": TokenKind.RPAREN,
    ",": TokenKind.COMMA,
    ";": TokenKind.SEMICOLON,
    "*": TokenKind.STAR,
    "=": TokenKind.EQUAL,
}

_WHITESPACE = " \t\r\n"


def tokenize(sql: str) -> tuple[Token, ...]:
    """Convierte una consulta en tokens e incluye siempre un token EOF."""

    return _Lexer(sql).tokenize()


class _Lexer:
    def __init__(self, source: str) -> None:
        self._source = source
        self._index = 0
        self._line = 1
        self._column = 1

    def tokenize(self) -> tuple[Token, ...]:
        tokens: list[Token] = []
        while not self._at_end:
            character = self._peek()
            if character in _WHITESPACE:
                self._advance()
            elif self._is_identifier_start(character):
                tokens.append(self._scan_word())
            elif character.isascii() and character.isdigit():
                tokens.append(self._scan_number())
            elif character == "-":
                if self._peek(1).isascii() and self._peek(1).isdigit():
                    tokens.append(self._scan_number())
                else:
                    self._raise_unexpected_character()
            elif character == "'":
                tokens.append(self._scan_string())
            elif character in _SINGLE_CHARACTER_TOKENS:
                tokens.append(self._scan_single_character())
            elif character in "<>":
                tokens.append(self._scan_comparison_operator())
            elif character == "!" and self._peek(1) == "=":
                self._raise_unsupported_operator()
            else:
                self._raise_unexpected_character()

        eof_span = Span(
            self._index,
            self._index,
            self._line,
            self._column,
            self._line,
            self._column,
        )
        tokens.append(Token(TokenKind.EOF, "", None, eof_span))
        return tuple(tokens)

    @property
    def _at_end(self) -> bool:
        return self._index >= len(self._source)

    def _peek(self, distance: int = 0) -> str:
        index = self._index + distance
        if index >= len(self._source):
            return ""
        return self._source[index]

    def _advance(self) -> str:
        character = self._source[self._index]
        self._index += 1

        if character == "\r":
            self._line += 1
            self._column = 1
        elif character == "\n":
            if self._index < 2 or self._source[self._index - 2] != "\r":
                self._line += 1
                self._column = 1
        else:
            self._column += 1
        return character

    def _mark(self) -> tuple[int, int, int]:
        return self._index, self._line, self._column

    def _span_from(self, mark: tuple[int, int, int]) -> Span:
        start, line, column = mark
        return Span(
            start,
            self._index,
            line,
            column,
            self._line,
            self._column,
        )

    def _token(
        self,
        kind: TokenKind,
        mark: tuple[int, int, int],
        value: TokenValue = None,
    ) -> Token:
        span = self._span_from(mark)
        return Token(kind, self._source[span.start : span.end], value, span)

    @staticmethod
    def _is_identifier_start(character: str) -> bool:
        return character == "_" or character.isalpha()

    @staticmethod
    def _is_identifier_part(character: str) -> bool:
        return (
            character == "_" or character.isalpha() or (character.isascii() and character.isdigit())
        )

    def _scan_word(self) -> Token:
        mark = self._mark()
        self._advance()
        while not self._at_end and self._is_identifier_part(self._peek()):
            self._advance()

        lexeme = self._source[mark[0] : self._index]
        kind = (
            _KEYWORDS.get(lexeme.upper(), TokenKind.IDENTIFIER)
            if lexeme.isascii()
            else TokenKind.IDENTIFIER
        )
        if kind is TokenKind.IDENTIFIER:
            return self._token(kind, mark, lexeme)
        if kind is TokenKind.TRUE:
            return self._token(kind, mark, True)
        if kind is TokenKind.FALSE:
            return self._token(kind, mark, False)
        return self._token(kind, mark)

    def _scan_number(self) -> Token:
        mark = self._mark()
        if self._peek() == "-":
            self._advance()
        while not self._at_end and self._peek().isascii() and self._peek().isdigit():
            self._advance()

        kind = TokenKind.INTEGER
        if self._peek() == ".":
            if not (self._peek(1).isascii() and self._peek(1).isdigit()):
                self._advance()
                span = self._span_from(mark)
                raise SQLLexError("literal numerico incompleto", span, self._source)
            kind = TokenKind.DOUBLE_LITERAL
            self._advance()
            while not self._at_end and self._peek().isascii() and self._peek().isdigit():
                self._advance()

        lexeme = self._source[mark[0] : self._index]
        try:
            if kind is TokenKind.INTEGER:
                value: int | float = int(lexeme)
            else:
                value = float(lexeme)
                significant_digits = (character for character in lexeme if character not in "-.")
                if not math.isfinite(value) or (
                    value == 0.0 and any(character != "0" for character in significant_digits)
                ):
                    raise OverflowError
        except (OverflowError, ValueError) as exc:
            literal_type = "entero" if kind is TokenKind.INTEGER else "double"
            raise SQLLexError(
                f"literal {literal_type} fuera de rango",
                self._span_from(mark),
                self._source,
            ) from exc
        return self._token(kind, mark, value)

    def _scan_string(self) -> Token:
        mark = self._mark()
        self._advance()
        value: list[str] = []

        while not self._at_end:
            character = self._advance()
            if character != "'":
                value.append(character)
                continue
            if self._peek() == "'":
                self._advance()
                value.append("'")
                continue
            return self._token(TokenKind.STRING, mark, "".join(value))

        raise SQLLexError("string sin cerrar", self._span_from(mark), self._source)

    def _scan_single_character(self) -> Token:
        mark = self._mark()
        kind = _SINGLE_CHARACTER_TOKENS[self._advance()]
        return self._token(kind, mark)

    def _scan_comparison_operator(self) -> Token:
        mark = self._mark()
        character = self._advance()
        if character == "<" and self._peek() == ">":
            self._advance()
            raise SQLUnsupportedError(
                "operador '<>' no soportado",
                self._span_from(mark),
                self._source,
            )
        has_equal = self._peek() == "="
        if has_equal:
            self._advance()

        if character == "<":
            kind = TokenKind.LESS_THAN_OR_EQUAL if has_equal else TokenKind.LESS_THAN
        else:
            kind = TokenKind.GREATER_THAN_OR_EQUAL if has_equal else TokenKind.GREATER_THAN
        return self._token(kind, mark)

    def _raise_unsupported_operator(self) -> None:
        mark = self._mark()
        self._advance()
        self._advance()
        raise SQLUnsupportedError(
            "operador '!=' no soportado",
            self._span_from(mark),
            self._source,
        )

    def _raise_unexpected_character(self) -> None:
        mark = self._mark()
        character = self._advance()
        raise SQLLexError(
            f"caracter inesperado {character!r}",
            self._span_from(mark),
            self._source,
        )
