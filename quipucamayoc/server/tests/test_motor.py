"""Cliente del API del motor (#243)."""

from __future__ import annotations

import csv
import io
import sys
from pathlib import Path

import httpx
import pytest

from app.motor import ClienteMotor, MotorError, MotorNoDisponible, literal, usa_tls

_RAIZ = Path(__file__).resolve().parents[3]


def _cliente(handler) -> ClienteMotor:
    return ClienteMotor("http://motor:8000/", transport=httpx.MockTransport(handler))


# ---------------------------------------------------------------------------
# Con un motor falso
# ---------------------------------------------------------------------------


def test_query_envia_el_sql_y_devuelve_filas_y_plan() -> None:
    def motor(peticion: httpx.Request) -> httpx.Response:
        assert peticion.method == "POST"
        assert peticion.url == "http://motor:8000/query"
        assert peticion.read() == b'{"sql":"SELECT id FROM papers"}'
        return httpx.Response(
            200,
            json={
                "columns": ["id", "titulo"],
                "column_types": ["INT", "VARCHAR"],
                "rows": [[1, "HNSW"], [2, "Faiss"]],
                "affected_rows": 0,
                "plan": {"root": {"op": "scan"}},
            },
        )

    resultado = _cliente(motor).query("SELECT id FROM papers")

    assert resultado.rows == ((1, "HNSW"), (2, "Faiss"))
    assert resultado.dicts() == [{"id": 1, "titulo": "HNSW"}, {"id": 2, "titulo": "Faiss"}]
    assert resultado.plan == {"root": {"op": "scan"}}


def test_un_error_del_motor_conserva_mensaje_y_ubicacion() -> None:
    cuerpo = {"error": "la tabla 'x' no existe", "kind": "semantic", "line": 1, "column": 15}

    with pytest.raises(MotorError) as atrapado:
        _cliente(lambda _: httpx.Response(400, json=cuerpo)).query("SELECT * FROM x")

    error = atrapado.value
    assert str(error) == "la tabla 'x' no existe"
    assert (error.kind, error.line, error.column, error.status) == ("semantic", 1, 15, 400)
    assert not isinstance(error, MotorNoDisponible)


def test_una_respuesta_rara_tambien_es_un_error_claro() -> None:
    with pytest.raises(MotorError, match="el motor respondio 502: Bad Gateway"):
        _cliente(lambda _: httpx.Response(502, text="Bad Gateway")).query("SELECT 1")


@pytest.mark.parametrize(
    ("excepcion", "mensaje"),
    [
        (httpx.ConnectError, "no se pudo contactar al motor en http://motor:8000"),
        (httpx.ReadTimeout, "no respondio a tiempo"),
    ],
)
def test_un_motor_caido_o_lento_es_motor_no_disponible(excepcion, mensaje: str) -> None:
    def motor(peticion: httpx.Request) -> httpx.Response:
        raise excepcion("x", request=peticion)

    with pytest.raises(MotorNoDisponible, match=mensaje):
        _cliente(motor).query("SELECT 1")


def test_load_csv_manda_un_csv_valido_y_lee_el_reporte() -> None:
    recibido: dict[str, object] = {}

    def motor(peticion: httpx.Request) -> httpx.Response:
        recibido["url"] = str(peticion.url)
        recibido["cuerpo"] = peticion.read()
        return httpx.Response(
            200,
            json={
                "table": "chunks",
                "encoding": "utf-8",
                "inserted": 1,
                "failed": 1,
                "errors": [{"line": 3, "error": "texto demasiado largo"}],
                "errors_truncated": False,
            },
        )

    carga = _cliente(motor).load_csv(
        "chunks",
        ["id", "texto"],
        [(1, 'coma, "comillas" y ñ'), (2, "x" * 5000)],
        atomic=True,
    )

    assert recibido["url"] == "http://motor:8000/tables/chunks/load?atomic=true"
    cuerpo = recibido["cuerpo"]
    assert isinstance(cuerpo, bytes)
    archivo = cuerpo.split(b"\r\n\r\n", 1)[1].rsplit(b"\r\n--", 1)[0].decode("utf-8")
    assert list(csv.reader(io.StringIO(archivo)))[:2] == [
        ["id", "texto"],
        ["1", 'coma, "comillas" y ñ'],
    ]
    assert (carga.inserted, carga.failed, carga.errors) == (1, 1, ((3, "texto demasiado largo"),))


# ---------------------------------------------------------------------------
# El escapado, contra el lexer real del motor
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def parse_sql():
    # El parser es Python puro: se usa el del repositorio como oraculo, sin
    # levantar el motor ni compilar el core.
    sys.path.insert(0, str(_RAIZ))
    try:
        from engine.parser import parse_sql
    finally:
        sys.path.remove(str(_RAIZ))
    return parse_sql


_PREGUNTAS_HOSTILES = [
    "¿Cuáles son las ventajas de HNSW sobre IVF?",
    "O'Neil's B-tree",
    "'); DROP TABLE papers; --",
    "x'; DELETE FROM chunks WHERE id = 1; SELECT '",
    "multi\nlinea /* comentario */ -- y mas",
    "''''",
    "nulo\x00medio",
]


@pytest.mark.parametrize("pregunta", _PREGUNTAS_HOSTILES)
def test_el_literal_llega_intacto_y_en_una_sola_sentencia(parse_sql, pregunta: str) -> None:
    sql = f"SELECT id FROM chunks WHERE MATCH(texto, {literal(pregunta)}) USING BM25 LIMIT 5"

    sentencia = parse_sql(sql)  # parse_sql exige exactamente una sentencia

    assert type(sentencia).__name__ == "SelectStatement"
    assert sentencia.where.query.value == pregunta.replace("\x00", "")


# ---------------------------------------------------------------------------
# Contra el motor real (ver motor_real en conftest.py)
# ---------------------------------------------------------------------------


def test_motor_real_crea_carga_y_busca_sin_inyeccion(motor_real) -> None:
    cliente, prefijo = motor_real
    tabla = f"{prefijo}docs"
    cliente.query(
        f"CREATE TABLE {tabla} (id INT PRIMARY KEY, texto VARCHAR(200)) USING HEAP;"
        f"CREATE INDEX {tabla}_texto ON {tabla} (texto) USING FULLTEXT WITH LANGUAGE=english"
    )
    carga = cliente.load_csv(
        tabla,
        ["id", "texto"],
        [(1, "Graph indexes, like HNSW, beat inverted files"), (2, "O'Neil's B-tree paper")],
    )
    assert (carga.inserted, carga.failed) == (2, 0)

    for pregunta in _PREGUNTAS_HOSTILES:
        cliente.query(f"SELECT id FROM {tabla} WHERE MATCH(texto, {literal(pregunta)}) LIMIT 5")

    assert cliente.table_names() >= {tabla}
    resultado = cliente.query(
        f"SELECT id, SCORE() AS relevancia FROM {tabla} "
        f"WHERE MATCH(texto, {literal('indexing graphs')}) USING BM25 LIMIT 5"
    )
    assert [fila[0] for fila in resultado.rows] == [1]
    assert cliente.query(f"SELECT id FROM {tabla} WHERE id = 2").rows == ((2,),)


def test_motor_real_responde_errores_con_ubicacion(motor_real) -> None:
    cliente, _ = motor_real

    with pytest.raises(MotorError) as atrapado:
        cliente.query("SELECT * FROM tabla_que_no_existe_jamas")

    assert atrapado.value.line == 1
    assert atrapado.value.column is not None


@pytest.mark.parametrize(
    ("url", "esperado"),
    [("http://127.0.0.1:8000", False), ("HTTPS://motor.example", True), ("https://x", True)],
)
def test_solo_se_cargan_certificados_para_https(url: str, esperado: bool) -> None:
    assert usa_tls(url) is esperado
