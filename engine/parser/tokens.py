"""Tokens producidos por el lexer SQL de QuipuDB."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TypeAlias

from engine.parser.span import Span


class TokenKind(StrEnum):
    """Clases lexicas del subconjunto SQL soportado."""

    IDENTIFIER = "IDENTIFIER"
    INTEGER = "INTEGER"
    DOUBLE_LITERAL = "DOUBLE_LITERAL"
    STRING = "STRING"

    CREATE = "CREATE"
    TABLE = "TABLE"
    PRIMARY = "PRIMARY"
    KEY = "KEY"
    USING = "USING"
    HEAP = "HEAP"
    SEQUENTIAL = "SEQUENTIAL"
    INSERT = "INSERT"
    INTO = "INTO"
    VALUES = "VALUES"
    SELECT = "SELECT"
    FROM = "FROM"
    JOIN = "JOIN"
    ON = "ON"
    WHERE = "WHERE"
    DELETE = "DELETE"
    ORDER = "ORDER"
    BY = "BY"
    ASC = "ASC"
    DESC = "DESC"
    GROUP = "GROUP"
    COUNT = "COUNT"
    SUM = "SUM"
    MIN = "MIN"
    MAX = "MAX"
    AVG = "AVG"
    BETWEEN = "BETWEEN"
    AND = "AND"
    INT = "INT"
    DOUBLE = "DOUBLE"
    VARCHAR = "VARCHAR"
    BOOL = "BOOL"
    DATE = "DATE"
    TRUE = "TRUE"
    FALSE = "FALSE"
    BEGIN = "BEGIN"
    TRANSACTION = "TRANSACTION"
    END = "END"

    LPAREN = "LPAREN"
    RPAREN = "RPAREN"
    COMMA = "COMMA"
    SEMICOLON = "SEMICOLON"
    STAR = "STAR"
    DOT = "DOT"

    EQUAL = "EQUAL"
    LESS_THAN = "LESS_THAN"
    LESS_THAN_OR_EQUAL = "LESS_THAN_OR_EQUAL"
    GREATER_THAN = "GREATER_THAN"
    GREATER_THAN_OR_EQUAL = "GREATER_THAN_OR_EQUAL"

    EOF = "EOF"


TokenValue: TypeAlias = str | int | float | bool | None


@dataclass(frozen=True, slots=True)
class Token:
    """Unidad lexica con el texto y la posicion exactos de la entrada.

    ``value`` contiene el valor semantico de identificadores y literales. Para
    las demas palabras reservadas, operadores, puntuacion y EOF es ``None``.
    """

    kind: TokenKind
    lexeme: str
    value: TokenValue
    span: Span
