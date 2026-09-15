"""Pruebas de la gramatica SQL y del contrato publico ``parse_sql``."""

from datetime import date

import pytest

from engine.parser import parse_sql
from engine.parser.ast import (
    AggregateCall,
    AggregateFunction,
    BeginTransactionStatement,
    BetweenCondition,
    BooleanLiteral,
    ColumnReference,
    ComparisonCondition,
    ComparisonOperator,
    CreateTableStatement,
    DateLiteral,
    DeleteStatement,
    DoubleLiteral,
    EndTransactionStatement,
    InsertStatement,
    IntegerLiteral,
    OrderDirection,
    SelectStatement,
    SqlTypeName,
    StorageKind,
    StringLiteral,
    Wildcard,
)
from engine.parser.errors import SQLParseError, SQLUnsupportedError


def test_create_table_parsea_tipos_primary_key_y_storage() -> None:
    sql = """CREATE TABLE Alumnos (
            codigo INT PRIMARY KEY,
            nombre VARCHAR(40),
            promedio DOUBLE,
            activo BOOL,
            ingreso DATE
        ) USING SEQUENTIAL;"""
    statement = parse_sql(sql)

    assert isinstance(statement, CreateTableStatement)
    assert statement.table.name == "Alumnos"
    assert statement.storage is StorageKind.SEQUENTIAL
    assert [column.name.name for column in statement.columns] == [
        "codigo",
        "nombre",
        "promedio",
        "activo",
        "ingreso",
    ]
    assert [column.data_type.name for column in statement.columns] == [
        SqlTypeName.INT,
        SqlTypeName.VARCHAR,
        SqlTypeName.DOUBLE,
        SqlTypeName.BOOL,
        SqlTypeName.DATE,
    ]
    assert statement.columns[0].primary_key is True
    assert statement.columns[1].data_type.length == 40
    assert all(not column.primary_key for column in statement.columns[1:])
    assert statement.span.start == 0
    assert statement.span.end == sql.index(";")


def test_create_table_usa_heap_por_defecto_y_keywords_son_case_insensitive() -> None:
    statement = parse_sql("create table datos (id int primary key)")

    assert isinstance(statement, CreateTableStatement)
    assert statement.storage is StorageKind.HEAP
    assert statement.table.name == "datos"


def test_parser_conserva_longitud_varchar_para_validacion_semantica_posterior() -> None:
    statement = parse_sql("CREATE TABLE datos (texto VARCHAR(0) PRIMARY KEY)")

    assert isinstance(statement, CreateTableStatement)
    assert statement.columns[0].data_type.length == 0


@pytest.mark.parametrize("primary_keys", [0, 2])
def test_parser_difiere_cantidad_de_primary_keys_a_semantica(primary_keys: int) -> None:
    first = " PRIMARY KEY" if primary_keys == 2 else ""
    second = " PRIMARY KEY" if primary_keys == 2 else ""

    statement = parse_sql(f"CREATE TABLE datos (a INT{first}, b INT{second}) USING HEAP")

    assert isinstance(statement, CreateTableStatement)
    assert sum(column.primary_key for column in statement.columns) == primary_keys
    assert statement.storage is StorageKind.HEAP


def test_insert_parsea_todos_los_literales_y_preserva_string() -> None:
    statement = parse_sql(
        "INSERT INTO alumnos VALUES (-7, 15.5, 'O''Brien', FALSE, DATE '2026-09-13');"
    )

    assert isinstance(statement, InsertStatement)
    assert statement.values == (
        IntegerLiteral(-7, statement.values[0].span),
        DoubleLiteral(15.5, statement.values[1].span),
        StringLiteral("O'Brien", statement.values[2].span),
        BooleanLiteral(False, statement.values[3].span),
        DateLiteral(date(2026, 9, 13), statement.values[4].span),
    )


def test_select_wildcard_sin_clausulas() -> None:
    statement = parse_sql("SELECT * FROM alumnos")

    assert isinstance(statement, SelectStatement)
    assert statement.table.name == "alumnos"
    assert isinstance(statement.projections[0], Wildcard)
    assert statement.where is None
    assert statement.group_by is None
    assert statement.order_by is None


@pytest.mark.parametrize(
    ("symbol", "operator"),
    [
        ("=", ComparisonOperator.EQUAL),
        ("<", ComparisonOperator.LESS_THAN),
        ("<=", ComparisonOperator.LESS_THAN_OR_EQUAL),
        (">", ComparisonOperator.GREATER_THAN),
        (">=", ComparisonOperator.GREATER_THAN_OR_EQUAL),
    ],
)
def test_select_parsea_proyeccion_y_comparaciones(
    symbol: str,
    operator: ComparisonOperator,
) -> None:
    statement = parse_sql(f"SELECT codigo, nombre FROM alumnos WHERE promedio {symbol} 15.5;")

    assert isinstance(statement, SelectStatement)
    assert [projection.name.name for projection in statement.projections] == [  # type: ignore[union-attr]
        "codigo",
        "nombre",
    ]
    assert isinstance(statement.where, ComparisonCondition)
    assert statement.where.column.name.name == "promedio"
    assert statement.where.operator is operator
    assert statement.where.value.value == 15.5


def test_select_parsea_between_inclusivo_sintacticamente() -> None:
    statement = parse_sql(
        "SELECT * FROM alumnos WHERE promedio BETWEEN 14 AND 18 ORDER BY promedio DESC"
    )

    assert isinstance(statement, SelectStatement)
    assert isinstance(statement.where, BetweenCondition)
    assert statement.where.lower.value == 14
    assert statement.where.upper.value == 18
    assert statement.order_by is not None
    assert statement.order_by.column.name.name == "promedio"
    assert statement.order_by.direction is OrderDirection.DESC


@pytest.mark.parametrize(
    ("function", "expected"),
    [
        ("COUNT", AggregateFunction.COUNT),
        ("SUM", AggregateFunction.SUM),
        ("MIN", AggregateFunction.MIN),
        ("MAX", AggregateFunction.MAX),
        ("AVG", AggregateFunction.AVG),
    ],
)
def test_select_parsea_agregados_y_group_by(
    function: str,
    expected: AggregateFunction,
) -> None:
    argument = "*" if function == "COUNT" else "promedio"
    statement = parse_sql(f"SELECT activo, {function}({argument}) FROM alumnos GROUP BY activo;")

    assert isinstance(statement, SelectStatement)
    assert isinstance(statement.projections[0], ColumnReference)
    aggregate = statement.projections[1]
    assert isinstance(aggregate, AggregateCall)
    assert aggregate.function is expected
    if function == "COUNT":
        assert isinstance(aggregate.argument, Wildcard)
    else:
        assert isinstance(aggregate.argument, ColumnReference)
    assert statement.group_by is not None
    assert statement.group_by.column.name.name == "activo"


@pytest.mark.parametrize(
    ("suffix", "direction"),
    [
        ("", OrderDirection.ASC),
        (" ASC", OrderDirection.ASC),
        (" DESC", OrderDirection.DESC),
    ],
)
def test_order_by_admite_direccion_opcional(
    suffix: str,
    direction: OrderDirection,
) -> None:
    statement = parse_sql(f"SELECT * FROM alumnos ORDER BY promedio{suffix}")

    assert isinstance(statement, SelectStatement)
    assert statement.order_by is not None
    assert statement.order_by.direction is direction


def test_delete_exige_y_parsea_where() -> None:
    statement = parse_sql("DELETE FROM alumnos WHERE codigo = 7;")

    assert isinstance(statement, DeleteStatement)
    assert statement.table.name == "alumnos"
    assert isinstance(statement.where, ComparisonCondition)
    assert statement.where.column.name.name == "codigo"
    assert statement.where.value.value == 7
    assert statement.where.span.start == len("DELETE FROM alumnos WHERE ")


def test_delete_admite_between() -> None:
    statement = parse_sql("DELETE FROM alumnos WHERE promedio BETWEEN 0 AND 10")

    assert isinstance(statement, DeleteStatement)
    assert isinstance(statement.where, BetweenCondition)
    assert statement.where.lower.value == 0
    assert statement.where.upper.value == 10


def test_begin_transaction_parsea_como_sentencia_propia() -> None:
    statement = parse_sql("BEGIN TRANSACTION;")

    assert isinstance(statement, BeginTransactionStatement)
    assert statement.span.start == 0
    assert statement.span.end == len("BEGIN TRANSACTION")


def test_end_transaction_parsea_como_sentencia_propia() -> None:
    statement = parse_sql("END TRANSACTION")

    assert isinstance(statement, EndTransactionStatement)
    assert statement.span.end == len("END TRANSACTION")


def test_spans_del_ast_apuntan_a_sus_fragmentos_originales() -> None:
    sql = (
        "SELECT activo, AVG(promedio) FROM alumnos "
        "WHERE promedio BETWEEN 14 AND 18 "
        "GROUP BY activo ORDER BY activo DESC;"
    )
    statement = parse_sql(sql)

    assert isinstance(statement, SelectStatement)
    assert sql[statement.span.start : statement.span.end] == sql[:-1]
    first_projection, aggregate = statement.projections
    assert sql[first_projection.span.start : first_projection.span.end] == "activo"
    assert isinstance(aggregate, AggregateCall)
    assert sql[aggregate.span.start : aggregate.span.end] == "AVG(promedio)"
    assert isinstance(aggregate.argument, ColumnReference)
    assert sql[aggregate.argument.span.start : aggregate.argument.span.end] == "promedio"
    assert isinstance(statement.where, BetweenCondition)
    assert sql[statement.where.span.start : statement.where.span.end] == (
        "promedio BETWEEN 14 AND 18"
    )
    assert sql[statement.where.lower.span.start : statement.where.lower.span.end] == "14"
    assert sql[statement.where.upper.span.start : statement.where.upper.span.end] == "18"
    assert statement.group_by is not None
    assert sql[statement.group_by.span.start : statement.group_by.span.end] == "GROUP BY activo"
    assert statement.order_by is not None
    assert sql[statement.order_by.span.start : statement.order_by.span.end] == (
        "ORDER BY activo DESC"
    )


@pytest.mark.parametrize(
    ("sql", "message"),
    [
        ("", "se esperaba CREATE TABLE"),
        ("CREATE alumnos (id INT)", "se esperaba TABLE"),
        ("CREATE TABLE alumnos ()", "nombre de una columna"),
        ("CREATE TABLE alumnos (id TEXT)", "se esperaba un tipo"),
        ("CREATE TABLE alumnos (id VARCHAR(-1))", "sin signo"),
        ("CREATE TABLE alumnos (id INT PRIMARY)", "se esperaba KEY"),
        ("CREATE TABLE alumnos (id INT) USING", "HEAP o SEQUENTIAL"),
        ("INSERT alumnos VALUES (1)", "se esperaba INTO"),
        ("INSERT INTO alumnos VALUES ()", "se esperaba un literal"),
        ("SELECT FROM alumnos", "columna o funcion de agregado"),
        ("SELECT * alumnos", "se esperaba FROM"),
        ("SELECT * FROM", "nombre de la tabla"),
        ("SELECT *, codigo FROM alumnos", "no se puede combinar"),
        ("SELECT SUM(*) FROM alumnos", "solo COUNT admite"),
        ("SELECT COUNT(codigo) FROM alumnos", "COUNT requiere"),
        ("SELECT * FROM alumnos WHERE codigo", "se esperaba ="),
        ("SELECT * FROM alumnos WHERE codigo BETWEEN 1 2", "se esperaba AND"),
        ("SELECT * FROM alumnos GROUP activo", "se esperaba BY"),
        ("SELECT * FROM alumnos ORDER BY", "se esperaba una columna"),
        ("DELETE FROM alumnos", "DELETE requiere"),
        ("DELETE alumnos WHERE id = 1", "se esperaba FROM"),
        ("BEGIN", "se esperaba TRANSACTION despues de BEGIN"),
        ("END", "se esperaba TRANSACTION despues de END"),
        ("INSERT INTO alumnos VALUES (DATE '20260913')", "formato"),
        ("INSERT INTO alumnos VALUES (DATE '٢٠٢٦-٠٩-١٣')", "formato"),
        ("INSERT INTO alumnos VALUES (DATE '2026-02-30')", "fecha DATE invalida"),
    ],
)
def test_consultas_invalidas_producen_error_claro(sql: str, message: str) -> None:
    with pytest.raises(SQLParseError, match=message) as caught:
        parse_sql(sql)

    assert caught.value.source == sql
    assert caught.value.offset <= len(sql)


def test_error_multilinea_indica_token_inesperado() -> None:
    sql = "SELECT *\nFROM alumnos\nWHERE promedio BETWEEN 14 18"

    with pytest.raises(SQLParseError, match="se esperaba AND") as caught:
        parse_sql(sql)

    assert caught.value.line == 3
    assert caught.value.column == 27
    assert "WHERE promedio BETWEEN 14 18" in str(caught.value)


def test_error_real_en_eof_conserva_span_vacio_y_caret() -> None:
    sql = "SELECT *\nFROM"

    with pytest.raises(SQLParseError, match="nombre de la tabla") as caught:
        parse_sql(sql)

    assert caught.value.offset == len(sql)
    assert caught.value.line == 2
    assert caught.value.column == 5
    assert caught.value.span.start == caught.value.span.end
    assert str(caught.value).splitlines()[-2:] == ["FROM", "    ^"]


def test_rechaza_multiples_sentencias_aunque_tengan_punto_y_coma() -> None:
    sql = "SELECT * FROM alumnos; DELETE FROM alumnos WHERE id = 1;"

    with pytest.raises(SQLParseError, match="solo se permite una sentencia") as caught:
        parse_sql(sql)

    assert caught.value.span.start == sql.index("DELETE")


@pytest.mark.parametrize(
    ("sql", "feature"),
    [
        ("UPDATE alumnos SET nombre = 'Ana'", "UPDATE"),
        ("SELECT * FROM alumnos JOIN cursos", "JOIN"),
        ("SELECT * FROM alumnos WHERE id = 1 AND activo = TRUE", "AND"),
        ("INSERT INTO alumnos VALUES (NULL)", "NULL"),
        ("SELECT * FROM alumnos LIMIT 1", "LIMIT"),
        ("SELECT update FROM alumnos", "UPDATE"),
        ("SELECT * FROM join", "JOIN"),
        ("CREATE INDEX por_id", "INDEX"),
        ("CREATE TABLE null (id INT)", "NULL"),
        ("SELECT * FROM alumnos WHERE or = 1", "OR"),
        ("COMMIT", "COMMIT"),
        ("ROLLBACK", "ROLLBACK"),
    ],
)
def test_caracteristicas_fuera_de_alcance_son_explicitas(sql: str, feature: str) -> None:
    with pytest.raises(SQLUnsupportedError, match=feature):
        parse_sql(sql)


def test_identificadores_preservan_casing_original() -> None:
    statement = parse_sql("SeLeCt Codigo FROM Alumnos WhErE Codigo = 1")

    assert isinstance(statement, SelectStatement)
    projection = statement.projections[0]
    assert isinstance(projection, ColumnReference)
    assert projection.name.name == "Codigo"
    assert statement.table.name == "Alumnos"


@pytest.mark.parametrize("identifier", ["ıN", "ıS", "ſelect"])
def test_identificadores_unicode_no_se_reinterpretan_como_keywords(identifier: str) -> None:
    statement = parse_sql(f"SELECT {identifier} FROM alumnos")

    assert isinstance(statement, SelectStatement)
    projection = statement.projections[0]
    assert isinstance(projection, ColumnReference)
    assert projection.name.name == identifier
