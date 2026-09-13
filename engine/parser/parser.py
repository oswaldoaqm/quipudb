"""Parser descendente recursivo para el subconjunto SQL de QuipuDB."""

from __future__ import annotations

import re
from datetime import date

from engine.parser.ast import (
    AggregateCall,
    AggregateFunction,
    BetweenCondition,
    BooleanLiteral,
    ColumnDefinition,
    ColumnReference,
    ComparisonCondition,
    ComparisonOperator,
    CreateTableStatement,
    DateLiteral,
    DeleteStatement,
    DoubleLiteral,
    GroupBy,
    Identifier,
    InsertStatement,
    IntegerLiteral,
    Literal,
    OrderBy,
    OrderDirection,
    Projection,
    SelectStatement,
    SqlType,
    SqlTypeName,
    Statement,
    StorageKind,
    StringLiteral,
    Wildcard,
)
from engine.parser.errors import SQLParseError, SQLUnsupportedError
from engine.parser.lexer import tokenize
from engine.parser.span import combine_spans
from engine.parser.tokens import Token, TokenKind

_DATE_PATTERN = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")

_AGGREGATES = {
    TokenKind.COUNT: AggregateFunction.COUNT,
    TokenKind.SUM: AggregateFunction.SUM,
    TokenKind.MIN: AggregateFunction.MIN,
    TokenKind.MAX: AggregateFunction.MAX,
    TokenKind.AVG: AggregateFunction.AVG,
}

_COMPARISONS = {
    TokenKind.EQUAL: ComparisonOperator.EQUAL,
    TokenKind.LESS_THAN: ComparisonOperator.LESS_THAN,
    TokenKind.LESS_THAN_OR_EQUAL: ComparisonOperator.LESS_THAN_OR_EQUAL,
    TokenKind.GREATER_THAN: ComparisonOperator.GREATER_THAN,
    TokenKind.GREATER_THAN_OR_EQUAL: ComparisonOperator.GREATER_THAN_OR_EQUAL,
}

_UNSUPPORTED_WORDS = {
    "ALTER",
    "AS",
    "BEGIN",
    "COMMIT",
    "DISTINCT",
    "DROP",
    "EXCEPT",
    "HAVING",
    "IN",
    "INDEX",
    "INTERSECT",
    "IS",
    "JOIN",
    "LIKE",
    "LIMIT",
    "NOT",
    "NULL",
    "OFFSET",
    "OR",
    "ROLLBACK",
    "UNION",
    "UPDATE",
    "VIEW",
}


def parse_sql(sql: str) -> Statement:
    """Convierte exactamente una sentencia SQL en un AST inmutable.

    Esta funcion solo analiza texto. No consulta el catalogo, no abre archivos y
    no importa los bindings nativos.
    """

    return _Parser(tokenize(sql), sql).parse()


class _Parser:
    def __init__(self, tokens: tuple[Token, ...], source: str) -> None:
        self._tokens = tokens
        self._source = source
        self._current = 0

    def parse(self) -> Statement:
        statement = self._statement()
        if self._match(TokenKind.SEMICOLON):
            if not self._check(TokenKind.EOF):
                raise self._error(
                    self._peek(),
                    "solo se permite una sentencia SQL por llamada",
                )
        elif not self._check(TokenKind.EOF):
            self._raise_trailing_input()
        self._expect(TokenKind.EOF, "se esperaba el final de la sentencia")
        return statement

    def _statement(self) -> Statement:
        if self._match(TokenKind.CREATE):
            return self._create_table(self._previous())
        if self._match(TokenKind.INSERT):
            return self._insert(self._previous())
        if self._match(TokenKind.SELECT):
            return self._select(self._previous())
        if self._match(TokenKind.DELETE):
            return self._delete(self._previous())

        token = self._peek()
        if self._word(token) in _UNSUPPORTED_WORDS:
            self._raise_unsupported(token)
        raise self._error(
            token,
            "se esperaba CREATE TABLE, INSERT INTO, SELECT o DELETE FROM",
        )

    def _create_table(self, start: Token) -> CreateTableStatement:
        self._expect(TokenKind.TABLE, "se esperaba TABLE despues de CREATE")
        table = self._identifier("se esperaba el nombre de la tabla")
        self._expect(TokenKind.LPAREN, "se esperaba '(' despues del nombre de la tabla")

        columns = [self._column_definition()]
        while self._match(TokenKind.COMMA):
            columns.append(self._column_definition())
        end = self._expect(TokenKind.RPAREN, "se esperaba ')' despues de las columnas")

        storage = StorageKind.HEAP
        if self._match(TokenKind.USING):
            if self._match(TokenKind.HEAP):
                storage = StorageKind.HEAP
            elif self._match(TokenKind.SEQUENTIAL):
                storage = StorageKind.SEQUENTIAL
            else:
                raise self._error(
                    self._peek(),
                    "se esperaba HEAP o SEQUENTIAL despues de USING",
                )
            end = self._previous()

        return CreateTableStatement(
            table=table,
            columns=tuple(columns),
            storage=storage,
            span=combine_spans(start.span, end.span),
        )

    def _column_definition(self) -> ColumnDefinition:
        name = self._identifier("se esperaba el nombre de una columna")
        data_type = self._sql_type()
        end_span = data_type.span
        primary_key = False
        if self._match(TokenKind.PRIMARY):
            end = self._expect(TokenKind.KEY, "se esperaba KEY despues de PRIMARY")
            primary_key = True
            end_span = end.span
        return ColumnDefinition(
            name=name,
            data_type=data_type,
            primary_key=primary_key,
            span=combine_spans(name.span, end_span),
        )

    def _sql_type(self) -> SqlType:
        if self._match(TokenKind.INT):
            token = self._previous()
            return SqlType(SqlTypeName.INT, None, token.span)
        if self._match(TokenKind.DOUBLE):
            token = self._previous()
            return SqlType(SqlTypeName.DOUBLE, None, token.span)
        if self._match(TokenKind.BOOL):
            token = self._previous()
            return SqlType(SqlTypeName.BOOL, None, token.span)
        if self._match(TokenKind.DATE):
            token = self._previous()
            return SqlType(SqlTypeName.DATE, None, token.span)
        if self._match(TokenKind.VARCHAR):
            start = self._previous()
            self._expect(TokenKind.LPAREN, "se esperaba '(' despues de VARCHAR")
            length_token = self._expect(
                TokenKind.INTEGER,
                "se esperaba una longitud entera para VARCHAR",
            )
            length = int(length_token.value)
            if length_token.lexeme.startswith("-"):
                raise self._error(
                    length_token,
                    "la longitud de VARCHAR debe ser un entero sin signo",
                )
            end = self._expect(
                TokenKind.RPAREN,
                "se esperaba ')' despues de la longitud de VARCHAR",
            )
            return SqlType(
                SqlTypeName.VARCHAR,
                length,
                combine_spans(start.span, end.span),
            )
        raise self._error(
            self._peek(),
            "se esperaba un tipo INT, DOUBLE, VARCHAR(n), BOOL o DATE",
        )

    def _insert(self, start: Token) -> InsertStatement:
        self._expect(TokenKind.INTO, "se esperaba INTO despues de INSERT")
        table = self._identifier("se esperaba el nombre de la tabla")
        self._expect(TokenKind.VALUES, "se esperaba VALUES despues del nombre de la tabla")
        self._expect(TokenKind.LPAREN, "se esperaba '(' despues de VALUES")

        values = [self._literal()]
        while self._match(TokenKind.COMMA):
            values.append(self._literal())
        end = self._expect(TokenKind.RPAREN, "se esperaba ')' despues de los valores")

        return InsertStatement(
            table=table,
            values=tuple(values),
            span=combine_spans(start.span, end.span),
        )

    def _select(self, start: Token) -> SelectStatement:
        projections = self._projection_list()
        self._expect(TokenKind.FROM, "se esperaba FROM despues de la proyeccion")
        table = self._identifier("se esperaba el nombre de la tabla")

        where = None
        group_by = None
        order_by = None
        end_span = table.span

        if self._match(TokenKind.WHERE):
            where = self._condition()
            end_span = where.span
        if self._match(TokenKind.GROUP):
            group_by = self._group_by(self._previous())
            end_span = group_by.span
        if self._match(TokenKind.ORDER):
            order_by = self._order_by(self._previous())
            end_span = order_by.span

        return SelectStatement(
            projections=tuple(projections),
            table=table,
            where=where,
            group_by=group_by,
            order_by=order_by,
            span=combine_spans(start.span, end_span),
        )

    def _projection_list(self) -> list[Projection]:
        if self._match(TokenKind.STAR):
            wildcard = Wildcard(self._previous().span)
            if self._check(TokenKind.COMMA):
                raise self._error(
                    self._peek(),
                    "'*' no se puede combinar con otras proyecciones",
                )
            return [wildcard]

        projections = [self._projection()]
        while self._match(TokenKind.COMMA):
            projections.append(self._projection())
        return projections

    def _projection(self) -> Projection:
        if self._peek().kind in _AGGREGATES:
            return self._aggregate()
        return self._column_reference("se esperaba una columna o funcion de agregado")

    def _aggregate(self) -> AggregateCall:
        function_token = self._advance()
        function = _AGGREGATES[function_token.kind]
        self._expect(TokenKind.LPAREN, "se esperaba '(' despues de la funcion de agregado")

        if function is AggregateFunction.COUNT:
            if not self._match(TokenKind.STAR):
                raise self._error(
                    self._peek(),
                    "COUNT requiere '*' como argumento",
                )
            wildcard_token = self._previous()
            argument: ColumnReference | Wildcard = Wildcard(wildcard_token.span)
        else:
            if self._match(TokenKind.STAR):
                raise self._error(
                    self._previous(),
                    "solo COUNT admite '*' como argumento",
                )
            argument = self._column_reference("se esperaba una columna como argumento del agregado")

        end = self._expect(
            TokenKind.RPAREN,
            "se esperaba ')' despues del argumento del agregado",
        )
        return AggregateCall(
            function=function,
            argument=argument,
            span=combine_spans(function_token.span, end.span),
        )

    def _group_by(self, start: Token) -> GroupBy:
        self._expect(TokenKind.BY, "se esperaba BY despues de GROUP")
        column = self._column_reference("se esperaba una columna despues de GROUP BY")
        return GroupBy(column, combine_spans(start.span, column.span))

    def _order_by(self, start: Token) -> OrderBy:
        self._expect(TokenKind.BY, "se esperaba BY despues de ORDER")
        column = self._column_reference("se esperaba una columna despues de ORDER BY")
        direction = OrderDirection.ASC
        end_span = column.span
        if self._match(TokenKind.ASC):
            end_span = self._previous().span
        elif self._match(TokenKind.DESC):
            direction = OrderDirection.DESC
            end_span = self._previous().span
        return OrderBy(column, direction, combine_spans(start.span, end_span))

    def _delete(self, start: Token) -> DeleteStatement:
        self._expect(TokenKind.FROM, "se esperaba FROM despues de DELETE")
        table = self._identifier("se esperaba el nombre de la tabla")
        self._expect(
            TokenKind.WHERE,
            "DELETE requiere una clausula WHERE",
        )
        where = self._condition()
        return DeleteStatement(
            table=table,
            where=where,
            span=combine_spans(start.span, where.span),
        )

    def _condition(self) -> ComparisonCondition | BetweenCondition:
        column = self._column_reference("se esperaba una columna en WHERE")
        if self._match(TokenKind.BETWEEN):
            lower = self._literal()
            self._expect(TokenKind.AND, "se esperaba AND en la condicion BETWEEN")
            upper = self._literal()
            return BetweenCondition(
                column=column,
                lower=lower,
                upper=upper,
                span=combine_spans(column.span, upper.span),
            )

        operator_token = self._peek()
        operator = _COMPARISONS.get(operator_token.kind)
        if operator is None:
            raise self._error(
                operator_token,
                "se esperaba =, <, <=, >, >= o BETWEEN en WHERE",
            )
        self._advance()
        value = self._literal()
        return ComparisonCondition(
            column=column,
            operator=operator,
            value=value,
            span=combine_spans(column.span, value.span),
        )

    def _literal(self) -> Literal:
        if self._match(TokenKind.INTEGER):
            token = self._previous()
            return IntegerLiteral(int(token.value), token.span)
        if self._match(TokenKind.DOUBLE_LITERAL):
            token = self._previous()
            return DoubleLiteral(float(token.value), token.span)
        if self._match(TokenKind.STRING):
            token = self._previous()
            return StringLiteral(str(token.value), token.span)
        if self._match(TokenKind.TRUE):
            token = self._previous()
            return BooleanLiteral(True, token.span)
        if self._match(TokenKind.FALSE):
            token = self._previous()
            return BooleanLiteral(False, token.span)
        if self._match(TokenKind.DATE):
            start = self._previous()
            value_token = self._expect(
                TokenKind.STRING,
                "se esperaba un string 'YYYY-MM-DD' despues de DATE",
            )
            value = str(value_token.value)
            if _DATE_PATTERN.fullmatch(value) is None:
                raise self._error(
                    value_token,
                    "el literal DATE debe usar el formato 'YYYY-MM-DD'",
                )
            try:
                parsed = date.fromisoformat(value)
            except ValueError as exc:
                raise self._error(value_token, f"fecha DATE invalida: {value}") from exc
            return DateLiteral(parsed, combine_spans(start.span, value_token.span))

        token = self._peek()
        if self._word(token) == "NULL":
            self._raise_unsupported(token)
        raise self._error(
            token,
            "se esperaba un literal entero, double, string, booleano o DATE",
        )

    def _identifier(self, message: str) -> Identifier:
        token = self._peek()
        if self._word(token) in _UNSUPPORTED_WORDS:
            self._raise_unsupported(token)
        token = self._expect(TokenKind.IDENTIFIER, message)
        return Identifier(str(token.value), token.span)

    def _column_reference(self, message: str) -> ColumnReference:
        identifier = self._identifier(message)
        return ColumnReference(identifier, identifier.span)

    def _raise_trailing_input(self) -> None:
        token = self._peek()
        if token.kind is TokenKind.AND:
            raise SQLUnsupportedError(
                "AND solo se admite como separador dentro de BETWEEN",
                token.span,
                self._source,
            )
        if self._word(token) in _UNSUPPORTED_WORDS:
            self._raise_unsupported(token)
        raise self._error(token, f"entrada inesperada {token.lexeme!r} al final de la sentencia")

    def _raise_unsupported(self, token: Token) -> None:
        feature = self._word(token) or token.lexeme
        raise SQLUnsupportedError(
            f"{feature} no esta soportado por el subconjunto SQL de QuipuDB",
            token.span,
            self._source,
        )

    @staticmethod
    def _word(token: Token) -> str:
        if token.kind is TokenKind.IDENTIFIER:
            value = str(token.value)
            return value.upper() if value.isascii() else ""
        if token.lexeme.isalpha():
            return token.lexeme.upper()
        return ""

    def _match(self, *kinds: TokenKind) -> bool:
        if not any(self._check(kind) for kind in kinds):
            return False
        self._advance()
        return True

    def _expect(self, kind: TokenKind, message: str) -> Token:
        if self._check(kind):
            return self._advance()
        token = self._peek()
        if self._word(token) in _UNSUPPORTED_WORDS:
            self._raise_unsupported(token)
        raise self._error(token, message)

    def _check(self, kind: TokenKind) -> bool:
        return self._peek().kind is kind

    def _advance(self) -> Token:
        token = self._peek()
        if token.kind is not TokenKind.EOF:
            self._current += 1
        return token

    def _peek(self) -> Token:
        return self._tokens[self._current]

    def _previous(self) -> Token:
        return self._tokens[self._current - 1]

    def _error(self, token: Token, message: str) -> SQLParseError:
        return SQLParseError(message, token.span, self._source)
