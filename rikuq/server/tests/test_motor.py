"""Cliente del API REST del motor (#302), sin motor.

Las peticiones se atienden con `httpx.MockTransport`, igual que hace
Quipucamayoc: las pruebas comprueban el SQL que se manda y como se leen las
respuestas, sin levantar QuipuDB.
"""

import httpx
import pytest

from app.config import Settings
from app.motor import (
    ClienteMotor,
    MotorError,
    MotorNoDisponible,
    Resultado,
    literal,
    literal_vector,
    usa_tls,
)


def _cliente(handler, **opciones) -> ClienteMotor:
    settings = Settings(quipudb_api_url="http://127.0.0.1:8000", **opciones)
    return ClienteMotor.desde(settings, transport=httpx.MockTransport(handler))


# --- literales ----------------------------------------------------------------


def test_literal_envuelve_en_comillas() -> None:
    assert literal("Aaron Peirsol") == "'Aaron Peirsol'"


def test_literal_duplica_la_comilla_simple() -> None:
    """Es lo unico que el lexer reconoce dentro de un literal."""

    assert literal("O'Brien") == "'O''Brien'"


def test_literal_no_deja_escapar_del_literal() -> None:
    """Un nombre con punto y coma o guiones sigue siendo un solo literal."""

    peligroso = "x'; DROP TABLE personas; --"

    escapado = literal(peligroso)

    assert escapado == "'x''; DROP TABLE personas; --'"
    assert escapado.count("'") % 2 == 0


def test_literal_quita_el_caracter_nulo() -> None:
    """No cabe en un VARCHAR y el motor lo rechazaria."""

    assert literal("a\x00b") == "'ab'"


def test_literal_vector_usa_decimal_fijo() -> None:
    """El lexer del motor no lee notacion cientifica."""

    texto = literal_vector([0.00003, -1.0, 0.5])

    assert "e" not in texto.lower().replace("vector", "")
    assert texto == "VECTOR(0.0000300, -1.0000000, 0.5000000)"


def test_literal_vector_conserva_la_precision_de_un_float32() -> None:
    """Siete decimales alcanzan porque los vectores estan normalizados."""

    componente = 0.1234567
    texto = literal_vector([componente] * 3)

    assert texto.count("0.1234567") == 3


def test_usa_tls_solo_con_https() -> None:
    assert usa_tls("https://ejemplo.test") is True
    assert usa_tls("http://127.0.0.1:8000") is False


# --- el cliente ---------------------------------------------------------------


def test_query_manda_el_sql_y_lee_las_filas() -> None:
    vistas: list[str] = []

    def handler(peticion: httpx.Request) -> httpx.Response:
        vistas.append(peticion.read().decode())
        assert peticion.url.path == "/query"
        return httpx.Response(
            200,
            json={
                "columns": ["id", "nombre"],
                "column_types": ["INT", "VARCHAR"],
                "rows": [[1, "Ana"], [2, "Beto"]],
                "affected_rows": 0,
            },
        )

    with _cliente(handler) as cliente:
        resultado = cliente.query("SELECT id, nombre FROM personas")

    assert '"sql"' in vistas[0]
    assert resultado.rows == ((1, "Ana"), (2, "Beto"))
    assert resultado.dicts() == [{"id": 1, "nombre": "Ana"}, {"id": 2, "nombre": "Beto"}]


def test_query_lee_cuantas_filas_cambio() -> None:
    def handler(_peticion: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"affected_rows": 3})

    with _cliente(handler) as cliente:
        assert cliente.query("INSERT INTO personas ...").affected_rows == 3


def test_table_names_devuelve_los_nombres() -> None:
    def handler(peticion: httpx.Request) -> httpx.Response:
        assert peticion.url.path == "/tables"
        return httpx.Response(200, json=[{"name": "personas"}, {"name": "rostros"}])

    with _cliente(handler) as cliente:
        assert cliente.table_names() == {"personas", "rostros"}


def test_un_sql_invalido_llega_con_el_mensaje_del_motor() -> None:
    def handler(_peticion: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"detail": "la columna 'x' no existe"})

    with _cliente(handler) as cliente, pytest.raises(MotorError) as error:
        cliente.query("SELECT x FROM personas")

    assert "la columna 'x' no existe" in str(error.value)
    assert error.value.status == 400
    assert error.value.sql == "SELECT x FROM personas"


def test_el_motor_apagado_se_distingue_de_un_sql_malo() -> None:
    """Ante esto hay que levantar el servidor, no corregir la consulta."""

    def handler(_peticion: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("conexion rechazada")

    with _cliente(handler) as cliente, pytest.raises(MotorNoDisponible) as error:
        cliente.query("SELECT 1")

    assert "revisa que este levantado" in str(error.value)


def test_un_timeout_tambien_es_motor_no_disponible() -> None:
    def handler(_peticion: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("tardo demasiado")

    with _cliente(handler) as cliente, pytest.raises(MotorNoDisponible, match="a tiempo"):
        cliente.query("SELECT 1")


def test_una_respuesta_que_no_es_json_no_revienta_el_cliente() -> None:
    def handler(_peticion: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>vaya</html>")

    with _cliente(handler) as cliente, pytest.raises(MotorError, match="no es JSON"):
        cliente.query("SELECT 1")


def test_el_resultado_vacio_es_usable() -> None:
    assert Resultado().dicts() == []
