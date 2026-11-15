"""Carga CSV por el coordinador (#292): reparto por fila, rechazos y nodo caído."""

import copy
import io

import httpx
import pytest
from fastapi.testclient import TestClient

from engine.distributed.catalog import DistributedCatalog
from engine.distributed.coordinator import Coordinator, create_coordinator_app
from engine.distributed.coordinator_test_support import FakeNode, error_body
from engine.distributed.csv_load import insert_sql, value_sql

CSV = "id,nombre\n5,cinco\n10000,diez mil\n9999,frontera\n"


def fakes(catalog, **overrides):
    nodes = {n.node_id: FakeNode(catalog, n.node_id) for n in catalog.nodes}
    for node_id, override in overrides.items():
        nodes[node_id].override = override
    return nodes


def api(catalog, nodes) -> TestClient:
    coordinator = Coordinator(catalog, transports={n: f.transport for n, f in nodes.items()})
    return TestClient(create_coordinator_app(coordinator))


def load(client, text, table="items", *, encoding="utf-8", **params):
    return client.post(
        f"/tables/{table}/load",
        files={"file": ("datos.csv", text.encode(encoding), "text/csv")},
        params=params,
    )


def inserts(node: FakeNode) -> list[str]:
    return [body["sql"] for request, body in node.requests if request.url.path.endswith("/execute")]


def test_cada_fila_va_como_insert_al_dueno_de_su_clave(node_catalog):
    nodes = fakes(node_catalog)
    with api(node_catalog, nodes) as client:
        response = load(client, CSV)
    assert response.status_code == 200, response.json()
    assert response.json() == {
        "table": "items",
        "encoding": "utf-8",
        "inserted": 3,
        "failed": 0,
        "errors": [],
        "errors_truncated": False,
    }
    assert inserts(nodes["n1"]) == [
        "INSERT INTO items VALUES (5, 'cinco')",
        "INSERT INTO items VALUES (9999, 'frontera')",
    ]
    assert inserts(nodes["n2"]) == ["INSERT INTO items VALUES (10000, 'diez mil')"]


def test_cabecera_en_otro_orden_comillas_y_lineas_en_blanco(node_catalog):
    nodes = fakes(node_catalog)
    texto = '\nNOMBRE;id\n"o\'brien; hijo";20000\n\n'
    with api(node_catalog, nodes) as client:
        response = load(client, texto)
    assert response.json()["inserted"] == 1
    assert inserts(nodes["n2"]) == ["INSERT INTO items VALUES (20000, 'o''brien; hijo')"]


def test_las_filas_que_no_convierten_no_llegan_a_ningun_nodo(node_catalog):
    nodes = fakes(node_catalog)
    texto = "id,nombre\nx12,malo\n7,bueno\n8\n99999999999,fuera de int\n"
    with api(node_catalog, nodes) as client:
        body = load(client, texto).json()
    assert (body["inserted"], body["failed"]) == (1, 3)
    assert [e["line"] for e in body["errors"]] == [2, 4, 5]
    assert "no es un INT" in body["errors"][0]["error"]
    assert inserts(nodes["n1"]) == ["INSERT INTO items VALUES (7, 'bueno')"]
    assert inserts(nodes["n2"]) == []


def test_un_rechazo_del_nodo_se_informa_en_su_linea_y_la_carga_sigue(node_catalog):
    def duplicada(request, body):
        if request.url.path.endswith("/execute") and "(5," in body["sql"]:
            data = error_body(nodes["n1"], body, "DUPLICATE_KEY", "unknown")
            return httpx.Response(409, json=data)
        return None

    nodes = fakes(node_catalog, n1=duplicada)
    with api(node_catalog, nodes) as client:
        body = load(client, CSV).json()
    assert (body["inserted"], body["failed"]) == (2, 1)
    assert body["errors"][0]["line"] == 2
    assert body["errors"][0]["error"].startswith("[DUPLICATE_KEY] nodo n1, fragmento items_f0")


def test_un_nodo_caido_no_se_vuelve_a_llamar_y_el_otro_sigue(node_catalog):
    def caido(request, body):
        if request.url.path.endswith("/execute"):
            raise httpx.ConnectError("rechazada", request=request)

    nodes = fakes(node_catalog, n2=caido)
    texto = "id,nombre\n1,a\n10000,b\n2,c\n20000,d\n30000,e\n"
    with api(node_catalog, nodes) as client:
        body = load(client, texto).json()
    assert (body["inserted"], body["failed"]) == (2, 3)
    assert [e["line"] for e in body["errors"]] == [3, 5, 6]
    assert body["errors"][0]["error"].startswith("[NODE_UNAVAILABLE] nodo n2")
    assert body["errors"][1]["error"] == "el nodo n2 dejó de responder en la línea 3; no se envió"
    # Un solo intento contra n2: las filas 5 y 6 no esperaron otro timeout.
    assert len(inserts(nodes["n2"])) == 1
    assert len(inserts(nodes["n1"])) == 2


def test_la_carga_atomica_se_rechaza_antes_de_enviar_nada(node_catalog):
    nodes = fakes(node_catalog)
    with api(node_catalog, nodes) as client:
        response = load(client, CSV, atomic="true")
    assert response.status_code == 400
    assert response.json()["error"].startswith("[UNSUPPORTED_DISTRIBUTED] la carga atómica")
    assert inserts(nodes["n1"]) == inserts(nodes["n2"]) == []


@pytest.mark.parametrize(
    ("text", "table", "status", "fragment"),
    [
        (CSV, "nada", 404, "no existe"),
        ("", "items", 400, "esta vacio"),
        ("id,otra\n1,x\n", "items", 400, "otra"),
    ],
)
def test_errores_del_archivo_con_la_forma_de_la_api_local(
    node_catalog, text, table, status, fragment
):
    nodes = fakes(node_catalog)
    with api(node_catalog, nodes) as client:
        response = load(client, text, table)
    assert response.status_code == status
    assert fragment in response.json()["error"]
    assert set(response.json()) == {"error", "kind", "line", "column", "end_line", "end_column"}
    assert inserts(nodes["n1"]) == []


def test_tipos_y_codificacion_de_excel(catalog_data):
    data = copy.deepcopy(catalog_data)
    data["tables"][0]["columns"].extend(
        [
            {"name": "precio", "type": "DOUBLE", "size": None},
            {"name": "fecha", "type": "DATE", "size": None},
            {"name": "activo", "type": "BOOL", "size": None},
        ]
    )
    catalog = DistributedCatalog.model_validate(data)
    nodes = fakes(catalog)
    texto = "id;nombre;precio;fecha;activo\n3;Añá;15,5;2026-11-14;1\n"
    with api(catalog, nodes) as client:
        body = load(client, texto, encoding="cp1252").json()
    assert (body["encoding"], body["inserted"]) == ("cp1252", 1)
    assert inserts(nodes["n1"]) == [
        "INSERT INTO items VALUES (3, 'Añá', 15.5, DATE '2026-11-14', TRUE)"
    ]


def test_una_tabla_single_manda_todo_a_su_dueno(catalog_data):
    data = copy.deepcopy(catalog_data)
    data["tables"][0].update(
        mode="single",
        fragments=[{"fragment_id": "items_f0", "node_id": "n2", "lower": None, "upper": None}],
    )
    catalog = DistributedCatalog.model_validate(data)
    nodes = fakes(catalog)
    with api(catalog, nodes) as client:
        assert load(client, CSV).json()["inserted"] == 3
    assert len(inserts(nodes["n2"])) == 3
    assert inserts(nodes["n1"]) == []


def test_literales():
    assert value_sql(1e-20) == "0.00000000000000000001"
    assert value_sql(False) == "FALSE"
    assert insert_sql("t", [1, "a'b"]) == "INSERT INTO t VALUES (1, 'a''b')"
    with pytest.raises(TypeError):
        value_sql([1.0, 2.0])


# -- contra nodos reales y la carga local ---------------------------------------


def test_mismo_informe_y_mismas_filas_que_la_carga_local(tmp_path, node_catalog, node_services):
    native = pytest.importorskip("quipudb_native")
    from engine.api.service import to_load_response, to_response
    from engine.distributed.node_api import create_node_app
    from engine.executor import QueryProcessor
    from engine.executor.native import to_native_schema

    texto = (
        "id,nombre\n5,cinco\n10000,diez mil\nx,mala\n5,repetida\n9999,frontera\n"
        "20000,veinte mil\n10000,repetida en n2\n"
    )
    table = node_catalog.table("items")
    (tmp_path / "local").mkdir()
    database = native.Database(tmp_path / "local" / "catalogo.txt")
    database.create_table(to_native_schema(table.bound_schema, native), table.storage)
    local = QueryProcessor(database)
    esperado = to_load_response(local.load_csv("items", io.StringIO(texto))).model_dump()

    coordinator = Coordinator(
        node_catalog,
        transports={
            n: httpx.ASGITransport(app=create_node_app(s)) for n, s in node_services.items()
        },
    )
    with TestClient(create_coordinator_app(coordinator)) as client:
        body = load(client, texto).json()
        filas = client.post("/query", json={"sql": "SELECT * FROM items ORDER BY id"}).json()
        fragmentos = client.get("/fragments").json()[0]["fragments"]

    assert (
        (body["inserted"], body["failed"]) == (esperado["inserted"], esperado["failed"]) == (4, 3)
    )
    assert [e["line"] for e in body["errors"]] == [e["line"] for e in esperado["errors"]]
    assert filas["rows"] == to_response(local.execute("SELECT * FROM items ORDER BY id")).rows
    assert [f["record_count"] for f in fragmentos] == [2, 2]
