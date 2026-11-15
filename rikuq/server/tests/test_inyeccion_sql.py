"""Que un nombre hostil termine guardado como texto y no ejecutado.

El API del motor no tiene parametros preparados: el nombre de una persona
--que sale del nombre de una carpeta del dataset, o sea de fuera-- termina
dentro de un ``INSERT``. Lo unico que lo contiene es ``literal()``.

La regla que se comprueba es simple: **el literal tiene que quedar
balanceado**. Si un nombre consigue cerrar su comilla, lo que venga despues
deja de ser texto y pasa a ser SQL.
"""

import pytest

from app.carga.dataset import insertar_persona, insertar_rostro
from app.carga.esquema import PERSONAS, ROSTROS
from app.consulta.identificar import consulta_sql
from app.motor import literal, literal_vector

HOSTILES = [
    "O'Brien",
    "x'; DROP TABLE personas; --",
    "a' OR '1'='1",
    "'' ; DELETE FROM rostros WHERE '1'='1",
    "'); DROP TABLE rostros; INSERT INTO personas VALUES (99, 'x', 'y', 1); --",
    'comillas "dobles" y `backticks`',
    "salto\nde\nlinea",
    "tabulacion\they",
    "punto; y; coma;",
    "guiones -- de -- comentario",
    "/* bloque */",
    "unicode: ñáéíóú 中文 🙂",
    "'" * 50,
]


def _balanceado(sql: str) -> bool:
    """Numero par de comillas simples: ningun literal quedo abierto."""

    return sql.count("'") % 2 == 0


@pytest.mark.parametrize("nombre", HOSTILES)
def test_el_literal_de_un_nombre_hostil_queda_balanceado(nombre) -> None:
    assert _balanceado(literal(nombre))


@pytest.mark.parametrize("nombre", HOSTILES)
def test_el_insert_de_una_persona_hostil_queda_balanceado(nombre) -> None:
    assert _balanceado(insertar_persona(1, nombre, "lfw", 2))


@pytest.mark.parametrize("nombre", HOSTILES)
def test_una_ruta_hostil_tampoco_rompe_el_insert(nombre) -> None:
    """La ruta tambien viene del disco, asi que tampoco es de fiar."""

    assert _balanceado(insertar_rostro(1, 1, nombre, [0.5, 0.5]))


def test_el_caracter_nulo_se_quita() -> None:
    """No cabe en un VARCHAR y el motor rechazaria la fila entera."""

    assert "\x00" not in literal("antes\x00despues")
    assert literal("antes\x00despues") == "'antesdespues'"


def test_la_comilla_se_duplica_y_no_se_escapa_con_barra() -> None:
    """La barra invertida no significa nada para el lexer del motor."""

    assert literal("a'b") == "'a''b'"
    assert "\\" not in literal("a'b")


def test_un_nombre_hostil_no_agrega_sentencias() -> None:
    """Lo peligroso no es la comilla: es el ';' que vendria despues."""

    sql = insertar_persona(1, "x'; DROP TABLE personas; --", "lfw", 1)

    # El ';' del nombre queda dentro del literal, asi que solo hay una
    # sentencia: la parte de fuera no contiene ninguno.
    fuera = sql.split("'")[0] + sql.rsplit("'", 1)[1]
    assert ";" not in fuera
    assert "DROP" not in fuera


def test_el_vector_no_admite_nada_que_no_sea_numero() -> None:
    """El vector llega de numpy, pero conviene que no haya forma de colar texto."""

    with pytest.raises((TypeError, ValueError)):
        literal_vector(["0.5); DROP TABLE rostros; --"])


def test_la_consulta_de_similitud_tambien_sale_balanceada() -> None:
    assert _balanceado(consulta_sql([0.1, -0.2, 0.3]))


def test_los_nombres_de_tabla_no_salen_de_datos_externos() -> None:
    """Van en constantes, no en algo que venga del dataset ni del usuario."""

    assert PERSONAS.isidentifier()
    assert ROSTROS.isidentifier()
