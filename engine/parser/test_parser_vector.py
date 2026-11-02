"""Sintaxis del tipo ``VECTOR(dim)`` y del literal ``VECTOR(...)`` (#180).

``VECTOR`` nombra el tipo en un CREATE TABLE y construye el literal en un
INSERT, igual que ``POINT`` desde #126. No hay ambiguedad porque la posicion
decide: en la declaracion de una columna va un entero, la dimension, y en una
lista de valores van las componentes.
"""

from __future__ import annotations

import pytest

from engine.parser.ast import (
    CreateTableStatement,
    InsertStatement,
    SqlTypeName,
    VectorLiteral,
)
from engine.parser.errors import SQLParseError
from engine.parser.parser import parse_sql


def _columnas(sql: str):
    statement = parse_sql(sql)
    assert isinstance(statement, CreateTableStatement)
    return statement.columns


def _valores(sql: str):
    statement = parse_sql(sql)
    assert isinstance(statement, InsertStatement)
    return statement.values


# ---------------------------------------------------------------------------
# El tipo
# ---------------------------------------------------------------------------


def test_vector_se_acepta_como_tipo_de_columna() -> None:
    columna = _columnas("CREATE TABLE fotos (id INT PRIMARY KEY, v VECTOR(128));")[1]
    assert columna.data_type.name is SqlTypeName.VECTOR
    assert columna.data_type.length == 128


def test_la_dimension_viaja_en_el_mismo_campo_que_la_longitud_de_varchar() -> None:
    # Es el mismo numero entre parentesis; lo que cambia es que cuenta
    # componentes y no bytes.
    columnas = _columnas(
        "CREATE TABLE t (id INT PRIMARY KEY, nombre VARCHAR(40), v VECTOR(13));"
    )
    assert columnas[1].data_type.length == 40
    assert columnas[2].data_type.length == 13


def test_una_tabla_puede_mezclar_el_vector_con_metadatos() -> None:
    # Criterio 4 del issue: nombre y ruta del objeto junto al vector.
    columnas = _columnas(
        "CREATE TABLE fotos (id INT PRIMARY KEY, nombre VARCHAR(60), "
        "ruta VARCHAR(200), tomada DATE, v VECTOR(128));"
    )
    assert [c.data_type.name for c in columnas] == [
        SqlTypeName.INT,
        SqlTypeName.VARCHAR,
        SqlTypeName.VARCHAR,
        SqlTypeName.DATE,
        SqlTypeName.VECTOR,
    ]


def test_el_nombre_del_tipo_no_distingue_mayusculas() -> None:
    assert _columnas("CREATE TABLE t (id INT PRIMARY KEY, v vector(3));")[1].data_type.name is (
        SqlTypeName.VECTOR
    )


# ---------------------------------------------------------------------------
# El tipo mal formado
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("sql", "fragmento"),
    [
        ("CREATE TABLE t (id INT PRIMARY KEY, v VECTOR);", "'(' despues de VECTOR"),
        ("CREATE TABLE t (id INT PRIMARY KEY, v VECTOR());", "dimension entera"),
        ("CREATE TABLE t (id INT PRIMARY KEY, v VECTOR(2.5));", "dimension entera"),
        ("CREATE TABLE t (id INT PRIMARY KEY, v VECTOR(x));", "dimension entera"),
        ("CREATE TABLE t (id INT PRIMARY KEY, v VECTOR(3 4));", "')' despues de la dimension"),
    ],
)
def test_un_tipo_mal_formado_se_rechaza_con_su_ubicacion(sql: str, fragmento: str) -> None:
    # Criterio 5: linea y columna, como el resto del parser.
    with pytest.raises(SQLParseError) as error:
        parse_sql(sql)
    assert fragmento in str(error.value)
    assert error.value.line == 1
    assert error.value.column is not None and error.value.column > 0


def test_el_tipo_aparece_en_el_mensaje_cuando_no_se_reconoce_ninguno() -> None:
    with pytest.raises(SQLParseError, match="VECTOR"):
        parse_sql("CREATE TABLE t (id INT PRIMARY KEY, v CUALQUIERA);")


# ---------------------------------------------------------------------------
# El literal
# ---------------------------------------------------------------------------


def test_un_literal_vectorial_guarda_sus_componentes_en_orden() -> None:
    literal = _valores("INSERT INTO fotos VALUES (VECTOR(0.5, -1, 2.25));")[0]
    assert isinstance(literal, VectorLiteral)
    assert literal.components == (0.5, -1.0, 2.25)


def test_las_componentes_enteras_se_leen_como_numeros() -> None:
    literal = _valores("INSERT INTO fotos VALUES (VECTOR(1, 2, 3));")[0]
    assert isinstance(literal, VectorLiteral)
    assert literal.components == (1.0, 2.0, 3.0)


def test_un_literal_de_una_sola_componente_es_valido() -> None:
    literal = _valores("INSERT INTO fotos VALUES (VECTOR(7));")[0]
    assert isinstance(literal, VectorLiteral)
    assert literal.components == (7.0,)


def test_cada_componente_sabe_donde_esta() -> None:
    # Es lo que permite que el error de dimension senale la primera que sobra
    # en vez del literal entero.
    literal = _valores("INSERT INTO fotos VALUES (VECTOR(1, 2, 3));")[0]
    assert isinstance(literal, VectorLiteral)
    assert len(literal.component_spans) == 3
    columnas = [span.column for span in literal.component_spans]
    assert columnas == sorted(columnas)


def test_el_literal_convive_con_otros_valores_en_la_misma_fila() -> None:
    valores = _valores("INSERT INTO fotos VALUES (1, 'a.jpg', VECTOR(0.1, 0.2));")
    assert len(valores) == 3
    assert isinstance(valores[2], VectorLiteral)


# ---------------------------------------------------------------------------
# El literal mal formado
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("sql", "fragmento"),
    [
        ("INSERT INTO t VALUES (VECTOR);", "'(' despues de VECTOR"),
        ("INSERT INTO t VALUES (VECTOR());", "componente 1"),
        ("INSERT INTO t VALUES (VECTOR(1, ));", "componente 2"),
        ("INSERT INTO t VALUES (VECTOR(1, 'x'));", "componente 2"),
        ("INSERT INTO t VALUES (VECTOR(1 2));", "')' despues de una componente"),
    ],
)
def test_un_literal_mal_formado_se_rechaza_con_su_ubicacion(sql: str, fragmento: str) -> None:
    with pytest.raises(SQLParseError) as error:
        parse_sql(sql)
    assert fragmento in str(error.value)
    assert error.value.line == 1


def test_el_mensaje_de_literal_desconocido_nombra_a_vector() -> None:
    # Quien escribe un valor que el motor no reconoce tiene que ver VECTOR
    # entre las opciones, o no sabra que existe.
    with pytest.raises(SQLParseError, match="VECTOR"):
        parse_sql("INSERT INTO t VALUES (abc);")
