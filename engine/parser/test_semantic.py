"""Pruebas del enlace semantico de CREATE TABLE e INSERT INTO."""

from dataclasses import FrozenInstanceError, replace
from datetime import date

import pytest

from engine.parser import parse_sql
from engine.parser.ast import (
    CreateTableStatement,
    DoubleLiteral,
    Identifier,
    InsertStatement,
    SqlType,
    SqlTypeName,
    StorageKind,
)
from engine.parser.bound_ast import (
    BoundColumn,
    BoundCreateTable,
    BoundInsertStatement,
    BoundSchema,
)
from engine.parser.errors import SQLSemanticError
from engine.parser.semantic import bind_create_table, bind_insert


def _create(sql: str) -> CreateTableStatement:
    statement = parse_sql(sql)
    assert isinstance(statement, CreateTableStatement)
    return statement


def _insert(sql: str) -> InsertStatement:
    statement = parse_sql(sql)
    assert isinstance(statement, InsertStatement)
    return statement


def _one_column_schema(
    data_type: SqlTypeName,
    length: int | None = None,
) -> BoundSchema:
    return BoundSchema("datos", (BoundColumn("valor", data_type, length),), 0)


@pytest.mark.parametrize(
    ("data_type", "length", "expected"),
    [
        (SqlTypeName.INT, None, 4),
        (SqlTypeName.DOUBLE, None, 8),
        (SqlTypeName.VARCHAR, 17, 17),
        (SqlTypeName.BOOL, None, 1),
        (SqlTypeName.DATE, None, 4),
    ],
)
def test_bound_column_refleja_tamano_del_core(
    data_type: SqlTypeName,
    length: int | None,
    expected: int,
) -> None:
    assert BoundColumn("valor", data_type, length).byte_size == expected


def test_bound_schema_suma_tamano_e_identifica_clave() -> None:
    schema = BoundSchema(
        "alumnos",
        (
            BoundColumn("id", SqlTypeName.INT, None),
            BoundColumn("nombre", SqlTypeName.VARCHAR, 40),
            BoundColumn("promedio", SqlTypeName.DOUBLE, None),
            BoundColumn("activo", SqlTypeName.BOOL, None),
            BoundColumn("ingreso", SqlTypeName.DATE, None),
        ),
        0,
    )

    assert schema.record_size == 57
    assert schema.key_column == 0
    with pytest.raises(FrozenInstanceError):
        schema.table_name = "otro"  # type: ignore[misc]


def test_bind_create_construye_esquema_inmutable_y_conserva_storage_y_span() -> None:
    source = (
        "CREATE TABLE alumnos (id INT PRIMARY KEY, nombre VARCHAR(40), "
        "promedio DOUBLE, activo BOOL, ingreso DATE) USING SEQUENTIAL"
    )
    statement = _create(source)

    bound = bind_create_table(statement, source)

    assert isinstance(bound, BoundCreateTable)
    assert bound.schema.table_name == "alumnos"
    assert [column.name for column in bound.schema.columns] == [
        "id",
        "nombre",
        "promedio",
        "activo",
        "ingreso",
    ]
    assert bound.schema.key_column == 0
    assert bound.storage is StorageKind.SEQUENTIAL
    assert bound.span == statement.span
    with pytest.raises(FrozenInstanceError):
        bound.storage = StorageKind.HEAP  # type: ignore[misc]


def test_identificador_de_64_caracteres_es_valido() -> None:
    table_name = "t" * 64
    bound = bind_create_table(_create(f"CREATE TABLE {table_name} (id INT PRIMARY KEY)"))

    assert bound.schema.table_name == table_name


@pytest.mark.parametrize("name", ["x" * 65, "Joseñ", "2datos", "con-guion", ""])
def test_bind_create_rechaza_nombre_de_tabla_incompatible_con_core(name: str) -> None:
    statement = _create("CREATE TABLE datos (id INT PRIMARY KEY)")
    statement = replace(statement, table=Identifier(name, statement.table.span))

    with pytest.raises(SQLSemanticError) as caught:
        bind_create_table(statement)

    assert caught.value.span == statement.table.span
    assert "tabla" in caught.value.message


def test_bind_create_rechaza_nombre_de_columna_incompatible_con_core() -> None:
    statement = _create("CREATE TABLE datos (codigo INT PRIMARY KEY)")
    original = statement.columns[0]
    invalid = replace(original, name=Identifier("código", original.name.span))
    statement = replace(statement, columns=(invalid,))

    with pytest.raises(SQLSemanticError) as caught:
        bind_create_table(statement)

    assert caught.value.span == invalid.name.span
    assert "columna" in caught.value.message


def test_columnas_repetidas_se_comparan_de_forma_exacta() -> None:
    duplicate = _create("CREATE TABLE datos (id INT PRIMARY KEY, id DOUBLE)")
    exact_case = _create("CREATE TABLE datos (id INT PRIMARY KEY, ID DOUBLE)")

    with pytest.raises(SQLSemanticError) as caught:
        bind_create_table(duplicate)

    assert caught.value.span == duplicate.columns[1].name.span
    assert "repetida" in caught.value.message
    assert [column.name for column in bind_create_table(exact_case).schema.columns] == ["id", "ID"]


def test_bind_create_rechaza_tabla_sin_columnas_aunque_el_ast_se_construya_a_mano() -> None:
    statement = _create("CREATE TABLE datos (id INT PRIMARY KEY)")
    statement = replace(statement, columns=())

    with pytest.raises(SQLSemanticError) as caught:
        bind_create_table(statement)

    assert caught.value.span == statement.span


@pytest.mark.parametrize(
    "sql",
    [
        "CREATE TABLE datos (id INT, nombre VARCHAR(8))",
        "CREATE TABLE datos (id INT PRIMARY KEY, otro INT PRIMARY KEY)",
    ],
)
def test_bind_create_exige_exactamente_una_primary_key(sql: str) -> None:
    statement = _create(sql)

    with pytest.raises(SQLSemanticError) as caught:
        bind_create_table(statement)

    assert "PRIMARY KEY" in caught.value.message
    if sum(column.primary_key for column in statement.columns) == 0:
        assert caught.value.span == statement.span
    else:
        assert caught.value.span == statement.columns[1].span


def test_bind_create_rechaza_varchar_sin_capacidad_positiva() -> None:
    statement = _create("CREATE TABLE datos (texto VARCHAR(0) PRIMARY KEY)")

    with pytest.raises(SQLSemanticError) as caught:
        bind_create_table(statement)

    assert caught.value.span == statement.columns[0].data_type.span
    assert "mayor que 0" in caught.value.message


def test_bind_create_rechaza_longitud_en_tipo_que_no_es_varchar() -> None:
    statement = _create("CREATE TABLE datos (id INT PRIMARY KEY)")
    column = statement.columns[0]
    invalid_type = SqlType(SqlTypeName.INT, 4, column.data_type.span)
    statement = replace(statement, columns=(replace(column, data_type=invalid_type),))

    with pytest.raises(SQLSemanticError, match="solo VARCHAR"):
        bind_create_table(statement)


@pytest.mark.parametrize(
    ("storage", "varchar_length", "record_size"),
    [
        ("HEAP", 4083, 4087),
        ("SEQUENTIAL", 2037, 2041),
    ],
)
def test_bind_create_acepta_registro_en_limite_fisico(
    storage: str,
    varchar_length: int,
    record_size: int,
) -> None:
    statement = _create(
        f"CREATE TABLE datos (id INT PRIMARY KEY, texto VARCHAR({varchar_length})) USING {storage}"
    )

    assert bind_create_table(statement).schema.record_size == record_size


@pytest.mark.parametrize(
    ("storage", "varchar_length", "limit"),
    [("HEAP", 4084, 4087), ("SEQUENTIAL", 2038, 2041)],
)
def test_bind_create_rechaza_registro_que_no_cabe_en_layout_del_core(
    storage: str,
    varchar_length: int,
    limit: int,
) -> None:
    statement = _create(
        f"CREATE TABLE datos (id INT PRIMARY KEY, texto VARCHAR({varchar_length})) USING {storage}"
    )

    with pytest.raises(SQLSemanticError) as caught:
        bind_create_table(statement)

    assert caught.value.span == statement.columns[1].data_type.span
    assert f"maximo de {limit}" in caught.value.message
    assert "4096" in caught.value.message


def test_bind_insert_convierte_todos_los_tipos_soportados() -> None:
    create = _create(
        "CREATE TABLE alumnos (id INT PRIMARY KEY, promedio DOUBLE, nombre VARCHAR(8), "
        "activo BOOL, ingreso DATE)"
    )
    schema = bind_create_table(create).schema
    statement = _insert(
        "INSERT INTO alumnos VALUES (-2147483648, 17, 'Ana', TRUE, DATE '2026-09-13')"
    )

    bound = bind_insert(statement, schema)

    assert isinstance(bound, BoundInsertStatement)
    assert bound.table_name == "alumnos"
    assert bound.values == (-2147483648, 17.0, "Ana", True, date(2026, 9, 13))
    assert type(bound.values[1]) is float
    assert bound.span == statement.span


def test_bind_insert_exige_la_tabla_del_esquema_y_senala_su_nombre() -> None:
    source = "INSERT INTO otra VALUES (1)"
    statement = _insert(source)

    with pytest.raises(SQLSemanticError) as caught:
        bind_insert(statement, _one_column_schema(SqlTypeName.INT), source)

    assert caught.value.span == statement.table.span
    assert "otra" in caught.value.message
    assert "datos" in caught.value.message
    assert "^" * len("otra") in str(caught.value)


@pytest.mark.parametrize("values", ["", "1, 2"])
def test_bind_insert_exige_aridad_exacta_y_senala_la_sentencia(values: str) -> None:
    if values:
        statement = _insert(f"INSERT INTO datos VALUES ({values})")
    else:
        statement = _insert("INSERT INTO datos VALUES (1)")
        schema = BoundSchema(
            "datos",
            (
                BoundColumn("uno", SqlTypeName.INT, None),
                BoundColumn("dos", SqlTypeName.INT, None),
            ),
            0,
        )
        with pytest.raises(SQLSemanticError) as caught:
            bind_insert(statement, schema)
        assert caught.value.span == statement.span
        return

    with pytest.raises(SQLSemanticError) as caught:
        bind_insert(statement, _one_column_schema(SqlTypeName.INT))
    assert caught.value.span == statement.span


@pytest.mark.parametrize("value", [-2147483648, 2147483647])
def test_int_admite_ambos_extremos_de_int32(value: int) -> None:
    statement = _insert(f"INSERT INTO datos VALUES ({value})")

    assert bind_insert(statement, _one_column_schema(SqlTypeName.INT)).values == (value,)


@pytest.mark.parametrize("value", [-2147483649, 2147483648])
def test_int_rechaza_valores_fuera_de_int32(value: int) -> None:
    statement = _insert(f"INSERT INTO datos VALUES ({value})")

    with pytest.raises(SQLSemanticError) as caught:
        bind_insert(statement, _one_column_schema(SqlTypeName.INT))

    assert caught.value.span == statement.values[0].span
    assert "32 bits" in caught.value.message


@pytest.mark.parametrize(("sql_value", "expected"), [("12", 12.0), ("12.5", 12.5)])
def test_double_acepta_entero_o_double_y_convierte_a_float(
    sql_value: str,
    expected: float,
) -> None:
    statement = _insert(f"INSERT INTO datos VALUES ({sql_value})")

    value = bind_insert(statement, _one_column_schema(SqlTypeName.DOUBLE)).values[0]

    assert value == expected
    assert type(value) is float


def test_double_rechaza_entero_que_desborda_float() -> None:
    huge = "9" * 400
    statement = _insert(f"INSERT INTO datos VALUES ({huge})")

    with pytest.raises(SQLSemanticError) as caught:
        bind_insert(statement, _one_column_schema(SqlTypeName.DOUBLE))

    assert caught.value.span == statement.values[0].span
    assert "desborda" in caught.value.message


@pytest.mark.parametrize("value", [float("inf"), float("-inf"), float("nan")])
def test_double_rechaza_valores_no_finitos_aunque_el_ast_se_construya_a_mano(
    value: float,
) -> None:
    statement = _insert("INSERT INTO datos VALUES (1.0)")
    literal = DoubleLiteral(value, statement.values[0].span)
    statement = replace(statement, values=(literal,))

    with pytest.raises(SQLSemanticError, match="finito") as caught:
        bind_insert(statement, _one_column_schema(SqlTypeName.DOUBLE))

    assert caught.value.span == literal.span


def test_varchar_mide_bytes_utf8_y_no_caracteres() -> None:
    schema = _one_column_schema(SqlTypeName.VARCHAR, 4)
    exact = _insert("INSERT INTO datos VALUES ('ññ')")
    too_long = _insert("INSERT INTO datos VALUES ('ñña')")

    assert bind_insert(exact, schema).values == ("ññ",)
    with pytest.raises(SQLSemanticError) as caught:
        bind_insert(too_long, schema)

    assert caught.value.span == too_long.values[0].span
    assert "5 bytes" in caught.value.message


def test_varchar_rechaza_byte_nulo() -> None:
    statement = _insert("INSERT INTO datos VALUES ('a\0b')")

    with pytest.raises(SQLSemanticError) as caught:
        bind_insert(statement, _one_column_schema(SqlTypeName.VARCHAR, 8))

    assert caught.value.span == statement.values[0].span
    assert "nulos" in caught.value.message


def test_varchar_rechaza_texto_que_no_se_puede_codificar_como_utf8() -> None:
    statement = _insert("INSERT INTO datos VALUES ('ok')")
    literal = replace(statement.values[0], value="\ud800")
    statement = replace(statement, values=(literal,))

    with pytest.raises(SQLSemanticError, match="UTF-8") as caught:
        bind_insert(statement, _one_column_schema(SqlTypeName.VARCHAR, 8))

    assert caught.value.span == literal.span


@pytest.mark.parametrize(
    ("data_type", "length", "sql_value", "received"),
    [
        (SqlTypeName.INT, None, "1.0", "DOUBLE"),
        (SqlTypeName.DOUBLE, None, "TRUE", "BOOL"),
        (SqlTypeName.VARCHAR, 8, "1", "INT"),
        (SqlTypeName.BOOL, None, "1", "INT"),
        (SqlTypeName.DATE, None, "'2026-09-13'", "VARCHAR"),
    ],
)
def test_bind_insert_rechaza_tipo_incompatible_y_senala_literal(
    data_type: SqlTypeName,
    length: int | None,
    sql_value: str,
    received: str,
) -> None:
    statement = _insert(f"INSERT INTO datos VALUES ({sql_value})")

    with pytest.raises(SQLSemanticError) as caught:
        bind_insert(statement, _one_column_schema(data_type, length))

    assert caught.value.span == statement.values[0].span
    assert f"se esperaba {data_type.value}" in caught.value.message
    assert f"se recibio {received}" in caught.value.message


def test_error_semantico_conserva_source_y_marca_literal_multilinea() -> None:
    source = "INSERT INTO datos VALUES (\n  2147483648\n)"
    statement = _insert(source)

    with pytest.raises(SQLSemanticError) as caught:
        bind_insert(statement, _one_column_schema(SqlTypeName.INT), source)

    assert caught.value.line == 2
    assert caught.value.column == 3
    assert "  2147483648\n  ^^^^^^^^^^" in str(caught.value)


def test_bound_column_varchar_incompleto_falla_de_forma_explicita() -> None:
    with pytest.raises(ValueError, match="longitud"):
        _ = BoundColumn("texto", SqlTypeName.VARCHAR, None).byte_size
