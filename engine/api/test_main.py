"""Pruebas de la API contra el core compilado (issue #99)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from engine.api.main import create_app
from engine.executor import QueryProcessor

quipudb = pytest.importorskip(
    "quipudb_native",
    reason="los bindings no estan compilados: cmake -DQUIPUDB_BUILD_PYTHON=ON",
)


@pytest.fixture()
def cliente(tmp_path):
    database = quipudb.Database(tmp_path / "catalogo.txt")
    app = create_app(
        processor=QueryProcessor(database),
        database=database,
        native=quipudb,
    )
    with TestClient(app) as cliente:
        yield cliente


def _poblar(cliente: TestClient) -> None:
    cliente.post("/query", json={"sql": (
        "CREATE TABLE alumnos (codigo INT PRIMARY KEY, nombre VARCHAR(32), "
        "promedio DOUBLE) USING HEAP"
    )})
    for i in range(3):
        cliente.post("/query", json={"sql": f"INSERT INTO alumnos VALUES ({i}, 'n{i}', {14.0 + i})"})


# ---------------------------------------------------------------------------
# GET /tables
# ---------------------------------------------------------------------------


def test_un_catalogo_vacio_devuelve_una_lista_vacia(cliente) -> None:
    respuesta = cliente.get("/tables")

    assert respuesta.status_code == 200
    assert respuesta.json() == []


def test_tables_describe_la_tabla_con_la_forma_que_espera_el_frontend(cliente) -> None:
    _poblar(cliente)

    tabla = cliente.get("/tables").json()[0]

    assert tabla["name"] == "alumnos"
    assert tabla["storage"] == "heap"
    assert tabla["record_count"] == 3
    assert tabla["columns"][0] == {
        "name": "codigo",
        "type": "INT",
        "size": None,
        "is_primary_key": True,
    }
    assert tabla["columns"][1]["size"] == 32
    assert tabla["indexes"] == []


def test_tables_lleva_los_indices_con_el_nombre_de_su_columna(cliente) -> None:
    _poblar(cliente)
    cliente.app.state.motor._database.create_index(
        "alumnos", "por_promedio", "promedio", quipudb.kind.BPLUS_UNCLUSTERED
    )

    indices = cliente.get("/tables").json()[0]["indexes"]

    assert indices == [
        {
            "name": "por_promedio",
            "column": "promedio",
            "structure": "bplus_unclustered",
            "supports_range": True,
        }
    ]


# ---------------------------------------------------------------------------
# POST /query
# ---------------------------------------------------------------------------


def test_un_select_devuelve_columnas_tipos_filas_y_plan(cliente) -> None:
    _poblar(cliente)

    cuerpo = cliente.post("/query", json={"sql": "SELECT nombre, promedio FROM alumnos"}).json()

    assert cuerpo["columns"] == ["nombre", "promedio"]
    assert cuerpo["column_types"] == ["VARCHAR", "DOUBLE"]
    assert cuerpo["rows"] == [["n0", 14.0], ["n1", 15.0], ["n2", 16.0]]
    assert cuerpo["affected_rows"] == 0
    assert cuerpo["plan"]["root"]["op"] == "project"


def test_un_resultado_vacio_conserva_los_tipos(cliente) -> None:
    _poblar(cliente)

    cuerpo = cliente.post(
        "/query", json={"sql": "SELECT nombre FROM alumnos WHERE codigo = 999"}
    ).json()

    assert cuerpo["rows"] == []
    assert cuerpo["column_types"] == ["VARCHAR"]


def test_una_sentencia_sin_filas_informa_en_affected_rows_y_manda_plan_null(cliente) -> None:
    _poblar(cliente)

    cuerpo = cliente.post(
        "/query", json={"sql": "INSERT INTO alumnos VALUES (9, 'zoe', 19.0)"}
    ).json()

    assert cuerpo["columns"] == []
    assert cuerpo["rows"] == []
    assert cuerpo["affected_rows"] == 1
    assert cuerpo["plan"] is None


def test_drop_table_actualiza_el_catalogo_expuesto_por_la_api(cliente) -> None:
    _poblar(cliente)

    respuesta = cliente.post("/query", json={"sql": "DROP TABLE alumnos"})

    assert respuesta.status_code == 200
    assert respuesta.json()["affected_rows"] == 0
    assert cliente.get("/tables").json() == []


def test_un_join_llega_por_http_con_su_paso_en_el_plan(cliente) -> None:
    _poblar(cliente)
    cliente.post("/query", json={"sql": (
        "CREATE TABLE cursos (id INT PRIMARY KEY, alumno INT, curso VARCHAR(16)) USING HEAP"
    )})
    cliente.post("/query", json={"sql": "INSERT INTO cursos VALUES (1, 1, 'bd2')"})

    cuerpo = cliente.post("/query", json={"sql": (
        "SELECT nombre, curso FROM alumnos JOIN cursos ON alumnos.codigo = cursos.alumno"
    )}).json()

    assert cuerpo["rows"] == [["n1", "bd2"]]
    assert cuerpo["plan"]["root"]["children"][0]["op"] == "join"


# ---------------------------------------------------------------------------
# Errores
# ---------------------------------------------------------------------------


def test_una_tabla_inexistente_responde_400_semantic_con_ubicacion(cliente) -> None:
    respuesta = cliente.post("/query", json={"sql": "SELECT * FROM inexistente"})

    assert respuesta.status_code == 400
    cuerpo = respuesta.json()
    assert cuerpo["kind"] == "semantic"
    assert cuerpo["line"] == 1
    assert cuerpo["column"] == 15
    assert "inexistente" in cuerpo["error"]


def test_una_palabra_fuera_del_subconjunto_responde_400_unsupported(cliente) -> None:
    respuesta = cliente.post("/query", json={"sql": "UPDATE alumnos SET nombre = 'x'"})

    assert respuesta.status_code == 400
    cuerpo = respuesta.json()
    assert cuerpo["kind"] == "unsupported"
    assert "UPDATE" in cuerpo["error"]


def test_la_ubicacion_del_error_subraya_el_fragmento_exacto(cliente) -> None:
    sql = "SELECT * FROM alumnos WHERE nombre LIKE 'a%'"

    cuerpo = cliente.post("/query", json={"sql": sql}).json()

    assert sql[cuerpo["column"] - 1 : cuerpo["end_column"] - 1] == "LIKE"


# ---------------------------------------------------------------------------
# CORS y estado
# ---------------------------------------------------------------------------


def test_el_origen_del_servidor_de_desarrollo_esta_permitido(cliente) -> None:
    respuesta = cliente.get("/tables", headers={"Origin": "http://localhost:5173"})

    assert respuesta.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_una_transaccion_sobrevive_entre_peticiones(cliente) -> None:
    """Es la razon de mantener un solo procesador por proceso."""

    _poblar(cliente)

    assert cliente.post("/query", json={"sql": "BEGIN TRANSACTION"}).status_code == 200
    cliente.post("/query", json={"sql": "INSERT INTO alumnos VALUES (7, 'siete', 17.0)"})
    assert cliente.post("/query", json={"sql": "END TRANSACTION"}).status_code == 200

    filas = cliente.post("/query", json={"sql": "SELECT codigo FROM alumnos"}).json()["rows"]
    assert [7] in filas
