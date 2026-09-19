"""Pruebas del modelo inmutable del AST SQL."""

from dataclasses import FrozenInstanceError
from datetime import date

import pytest

from engine.parser.ast import (
    AggregateCall,
    AggregateFunction,
    BeginTransactionStatement,
    BetweenCondition,
    BooleanLiteral,
    ColumnDefinition,
    ColumnReference,
    ComparisonCondition,
    ComparisonOperator,
    CreateIndexStatement,
    CreateTableStatement,
    DateLiteral,
    DeleteStatement,
    DoubleLiteral,
    DropTableStatement,
    EndTransactionStatement,
    ExplainStatement,
    GroupBy,
    Identifier,
    IndexKind,
    InsertStatement,
    IntegerLiteral,
    OrderBy,
    OrderDirection,
    SelectStatement,
    SqlType,
    SqlTypeName,
    StorageKind,
    StringLiteral,
    TableRef,
    Wildcard,
)
from engine.parser.span import Span, combine_spans


def span(start: int, end: int, column: int = 1) -> Span:
    return Span(start, end, 1, column, 1, column + end - start)


def test_span_combina_extremos_y_permite_eof_vacio() -> None:
    first = Span(7, 10, 2, 3, 2, 6)
    last = Span(14, 14, 3, 1, 3, 1)

    assert first.through(last) == Span(7, 14, 2, 3, 3, 1)
    assert combine_spans(last, first) == Span(7, 14, 2, 3, 3, 1)
    assert last.start == last.end


@pytest.mark.parametrize(
    "invalid",
    [
        (-1, 0, 1, 1, 1, 2),
        (2, 1, 1, 1, 1, 1),
        (0, 1, 0, 1, 1, 2),
        (0, 1, 2, 1, 1, 2),
        (0, 1, 1, 3, 1, 2),
    ],
)
def test_span_rechaza_posiciones_imposibles(invalid: tuple[int, ...]) -> None:
    with pytest.raises(ValueError):
        Span(*invalid)


def test_nodos_tienen_igualdad_por_valor_y_son_inmutables() -> None:
    identifier = Identifier("alumnos", span(0, 7))
    same_identifier = Identifier("alumnos", span(0, 7))

    assert identifier == same_identifier
    assert hash(identifier) == hash(same_identifier)
    with pytest.raises(FrozenInstanceError):
        identifier.name = "cursos"  # type: ignore[misc]


def test_ast_representa_todas_las_sentencias_del_alcance() -> None:
    table = Identifier("alumnos", span(13, 20, 14))
    column_name = Identifier("codigo", span(22, 28, 23))
    column = ColumnReference(column_name, column_name.span)
    integer = IntegerLiteral(42, span(31, 33, 32))
    condition = ComparisonCondition(
        column,
        ComparisonOperator.EQUAL,
        integer,
        combine_spans(column.span, integer.span),
    )
    definition = ColumnDefinition(
        column_name,
        SqlType(SqlTypeName.INT, None, column_name.span),
        True,
        column_name.span,
    )

    create = CreateTableStatement(table, (definition,), StorageKind.HEAP, span(0, 34))
    create_index = CreateIndexStatement(
        Identifier("por_codigo", span(13, 23, 14)),
        table,
        column_name,
        IndexKind.BPLUS_UNCLUSTERED,
        span(0, 54),
    )
    insert = InsertStatement(table, (integer,), span(0, 34))
    select = SelectStatement(
        (Wildcard(span(7, 8, 8)),),
        TableRef(table, table.span),
        condition,
        None,
        OrderBy(column, OrderDirection.DESC, span(34, 54, 35)),
        span(0, 54),
    )
    delete = DeleteStatement(table, condition, span(0, 33))
    drop = DropTableStatement(table, span(0, 20))
    begin = BeginTransactionStatement(span(0, 18))
    end = EndTransactionStatement(span(0, 16))
    explain = ExplainStatement(select, True, span(0, 62))

    assert create.columns == (definition,)
    assert create_index.column == column_name
    assert create_index.kind is IndexKind.BPLUS_UNCLUSTERED
    assert insert.values == (integer,)
    assert select.projections == (Wildcard(span(7, 8, 8)),)
    assert delete.where == condition
    assert drop.table == table
    assert begin.span == span(0, 18)
    assert end.span == span(0, 16)
    assert explain.statement == select
    assert explain.analyze is True


def test_ast_representa_literales_agregados_rangos_y_agrupacion() -> None:
    name = Identifier("promedio", span(7, 15, 8))
    column = ColumnReference(name, name.span)
    literals = (
        IntegerLiteral(-2, span(0, 2)),
        DoubleLiteral(15.5, span(0, 4)),
        StringLiteral("O'Brien", span(0, 10)),
        BooleanLiteral(True, span(0, 4)),
        DateLiteral(date(2026, 9, 13), span(0, 17)),
    )
    aggregate = AggregateCall(AggregateFunction.AVG, column, span(3, 16, 4))
    between = BetweenCondition(column, literals[0], literals[1], span(7, 33, 8))
    group = GroupBy(column, span(35, 52, 36))

    assert aggregate.argument == column
    assert between.lower.value == -2
    assert group.column.name.name == "promedio"
