"""Enlace semantico de ``VECTOR(dim)`` y sus valores (#180).

La dimension es parte del TIPO, como la capacidad de un VARCHAR. De ahi sale
la regla que ordena todas estas pruebas: un vector de otra dimension no es un
valor fuera de rango, es un valor de otro tipo, y el error tiene que senalar
donde esta el problema.
"""

from __future__ import annotations

import math

import pytest

from engine.parser.ast import SqlTypeName, VectorLiteral
from engine.parser.bound_ast import VECTOR_COMPONENT_SIZE, BoundColumn, BoundSchema
from engine.parser.errors import SQLLexError, SQLSemanticError
from engine.parser.parser import parse_sql
from engine.parser.semantic import (
    _bind_value,
    bind_create_index,
    bind_create_table,
    bind_insert,
)
from engine.parser.span import Span

FOTOS = BoundSchema(
    "fotos",
    (
        BoundColumn("id", SqlTypeName.INT, None),
        BoundColumn("ruta", SqlTypeName.VARCHAR, 80),
        BoundColumn("rasgos", SqlTypeName.VECTOR, 3),
    ),
    0,
)


def _insert(valores: str):
    return bind_insert(parse_sql(f"INSERT INTO fotos VALUES ({valores});"), FOTOS)


# ---------------------------------------------------------------------------
# El catalogo guarda la dimension
# ---------------------------------------------------------------------------


def test_el_esquema_conserva_la_dimension_declarada() -> None:
    esquema = bind_create_table(
        parse_sql("CREATE TABLE fotos (id INT PRIMARY KEY, v VECTOR(128));")
    ).schema
    assert esquema.columns[1].data_type is SqlTypeName.VECTOR
    assert esquema.columns[1].length == 128


def test_una_columna_vectorial_ocupa_cuatro_bytes_por_componente() -> None:
    # float32 y no double: SIFT, MFCC y el histograma de #181 ya trabajan en
    # float32, asi que guardar en double duplicaria el registro sin agregar
    # informacion.
    assert VECTOR_COMPONENT_SIZE == 4
    assert BoundColumn("v", SqlTypeName.VECTOR, 128).byte_size == 512


def test_el_tamano_del_registro_incluye_el_vector() -> None:
    assert FOTOS.record_size == 4 + 80 + 3 * VECTOR_COMPONENT_SIZE


def test_una_tabla_puede_mezclar_el_vector_con_metadatos() -> None:
    # Criterio 4 del issue.
    esquema = bind_create_table(
        parse_sql(
            "CREATE TABLE fotos (id INT PRIMARY KEY, nombre VARCHAR(60), "
            "ruta VARCHAR(200), v VECTOR(64));"
        )
    ).schema
    assert [c.name for c in esquema.columns] == ["id", "nombre", "ruta", "v"]


# ---------------------------------------------------------------------------
# Dimensiones invalidas al declarar
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("dimension", [0, -1])
def test_una_dimension_no_positiva_se_rechaza(dimension: int) -> None:
    sql = f"CREATE TABLE t (id INT PRIMARY KEY, v VECTOR({dimension}));"
    with pytest.raises(Exception) as error:
        bind_create_table(parse_sql(sql))
    assert "VECTOR" in str(error.value)


def test_un_vector_que_no_cabe_en_una_pagina_se_rechaza_al_crear_la_tabla() -> None:
    # Sin esto el error apareceria recien al abrir el archivo, lejos de la
    # sentencia que lo causo. La maquinaria de limite por pagina ya existia;
    # lo unico nuevo es que VECTOR sabe cuanto ocupa.
    with pytest.raises(SQLSemanticError) as error:
        bind_create_table(parse_sql("CREATE TABLE t (id INT PRIMARY KEY, v VECTOR(2000));"))
    assert "supera el maximo" in str(error.value)
    assert error.value.column is not None


def test_la_dimension_maxima_depende_de_lo_que_ocupen_las_demas_columnas() -> None:
    # 4087 bytes de registro maximo en HEAP, menos los 4 del INT, dan 1020
    # componentes justas.
    bind_create_table(parse_sql("CREATE TABLE t (id INT PRIMARY KEY, v VECTOR(1020));"))
    with pytest.raises(SQLSemanticError):
        bind_create_table(parse_sql("CREATE TABLE t (id INT PRIMARY KEY, v VECTOR(1021));"))


# ---------------------------------------------------------------------------
# Insertar un vector
# ---------------------------------------------------------------------------


def test_un_vector_de_la_dimension_declarada_entra() -> None:
    assert _insert("1, 'a.jpg', VECTOR(0.5, -1, 2.25)").values == (1, "a.jpg", (0.5, -1.0, 2.25))


def test_las_componentes_llegan_como_numeros_en_orden() -> None:
    valores = _insert("1, 'a.jpg', VECTOR(3, 1, 2)").values
    assert valores[2] == (3.0, 1.0, 2.0)


def test_un_vector_con_menos_componentes_se_rechaza() -> None:
    with pytest.raises(SQLSemanticError) as error:
        _insert("1, 'a.jpg', VECTOR(1, 2)")
    assert "2 componentes" in str(error.value)
    assert "VECTOR(3)" in str(error.value)


def test_un_vector_con_mas_componentes_se_rechaza() -> None:
    with pytest.raises(SQLSemanticError) as error:
        _insert("1, 'a.jpg', VECTOR(1, 2, 3, 4)")
    assert "4 componentes" in str(error.value)


def test_el_error_de_dimension_senala_la_primera_componente_que_sobra() -> None:
    sql = "INSERT INTO fotos VALUES (1, 'a.jpg', VECTOR(1, 2, 3, 4));"
    with pytest.raises(SQLSemanticError) as error:
        bind_insert(parse_sql(sql), FOTOS)
    # La cuarta componente es el '4'; el span tiene que caer sobre el, no
    # sobre el literal entero.
    assert error.value.column is not None
    assert sql[error.value.column - 1] == "4"


def test_el_error_de_dimension_senala_el_literal_cuando_faltan_componentes() -> None:
    sql = "INSERT INTO fotos VALUES (1, 'a.jpg', VECTOR(1, 2));"
    with pytest.raises(SQLSemanticError) as error:
        bind_insert(parse_sql(sql), FOTOS)
    assert error.value.column is not None
    assert sql[error.value.column - 1 :].startswith("VECTOR")


# ---------------------------------------------------------------------------
# Componentes que no son valores
# ---------------------------------------------------------------------------


def _literal_con(componentes: tuple[float, ...]) -> VectorLiteral:
    """Construye el literal a mano, sin pasar por el texto SQL.

    El lexer rechaza un double fuera de rango antes de que llegue aqui, asi
    que esta comprobacion no se alcanza escribiendo SQL. Se conserva igual,
    como segunda linea de defensa: ``bind_insert`` tambien lo llama la API con
    arboles construidos en memoria, y es la misma regla que ya aplica DOUBLE
    con NaN. El core la vuelve a exigir en ``Schema::validate``.
    """

    span = Span(0, 1, 1, 1, 1, 2)
    return VectorLiteral(componentes, tuple(span for _ in componentes), span)


@pytest.mark.parametrize("malo", [math.inf, -math.inf, math.nan])
def test_una_componente_no_finita_se_rechaza(malo: float) -> None:
    # Un infinito o un NaN haria falsa cualquier distancia de #179 y el
    # objeto quedaria en un lugar arbitrario del ranking sin que nada lo
    # denuncie.
    literal = _literal_con((1.0, malo, 3.0))
    with pytest.raises(SQLSemanticError, match="no es finita"):
        _bind_value(literal, FOTOS.columns[2], None)


def test_el_error_de_componente_no_finita_dice_cual_es() -> None:
    with pytest.raises(SQLSemanticError, match="componente 2"):
        _bind_value(_literal_con((1.0, math.nan, 3.0)), FOTOS.columns[2], None)


def test_un_vector_de_componentes_finitas_pasa_la_validacion() -> None:
    assert _bind_value(_literal_con((1.0, 2.0, 3.0)), FOTOS.columns[2], None) == (1.0, 2.0, 3.0)


def test_el_lexer_ya_rechaza_un_double_fuera_de_rango() -> None:
    # Documenta por que las pruebas de arriba no pasan por SQL escrito.
    with pytest.raises(SQLLexError, match="fuera de rango"):
        parse_sql(f"INSERT INTO fotos VALUES (1, 'a.jpg', VECTOR(1, {'1' + '0' * 400}.0, 3));")


# ---------------------------------------------------------------------------
# Tipos cruzados
# ---------------------------------------------------------------------------


def test_un_valor_de_otro_tipo_en_la_columna_vectorial_se_rechaza() -> None:
    with pytest.raises(SQLSemanticError, match="VECTOR"):
        _insert("1, 'a.jpg', 3.5")


def test_un_punto_no_sirve_como_vector() -> None:
    with pytest.raises(SQLSemanticError, match="VECTOR"):
        _insert("1, 'a.jpg', POINT(1, 2)")


def test_un_vector_no_sirve_como_otro_tipo() -> None:
    with pytest.raises(SQLSemanticError):
        _insert("VECTOR(1, 2, 3), 'a.jpg', VECTOR(1, 2, 3)")


# ---------------------------------------------------------------------------
# Indices sobre una columna vectorial
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("estructura", ["BPLUS", "BPLUS_UNCLUSTERED", "HASH", "EXTENDIBLE_HASH"])
def test_un_indice_escalar_sobre_un_vector_se_rechaza(estructura: str) -> None:
    # Un vector se compara por similitud: un B+ o un hash sobre el no
    # responderia ninguna consulta util, y un rango entre dos vectores no
    # significa nada. Los indices que si le sirven son IVF y HNSW (#183 a
    # #186), que todavia no existen.
    sentencia = parse_sql(f"CREATE INDEX ix ON fotos (rasgos) USING {estructura};")
    with pytest.raises(SQLSemanticError, match="similitud"):
        bind_create_index(sentencia, FOTOS, "heap")


def test_el_error_del_indice_nombra_la_estructura_pedida() -> None:
    sentencia = parse_sql("CREATE INDEX ix ON fotos (rasgos) USING HASH;")
    with pytest.raises(SQLSemanticError, match="EXTENDIBLE_HASH"):
        bind_create_index(sentencia, FOTOS, "heap")


def test_las_demas_columnas_se_siguen_pudiendo_indexar() -> None:
    sentencia = parse_sql("CREATE INDEX ix ON fotos (ruta) USING BPLUS;")
    assert bind_create_index(sentencia, FOTOS, "heap").column_name == "ruta"
