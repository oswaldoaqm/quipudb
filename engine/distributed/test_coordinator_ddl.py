"""DDL distribuido desde ``POST /query`` (#296), con dos nodos reales en memoria."""

import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from engine.distributed.catalog_store import newest_catalog
from engine.distributed.coordinator import Coordinator, create_coordinator_app
from engine.distributed.node_api import create_node_app

VENTAS = (
    "CREATE TABLE ventas (id INT PRIMARY KEY, monto DOUBLE) USING HEAP "
    "PARTITION BY RANGE (id) ("
    "PARTITION ventas_f0 VALUES LESS THAN (5000) ON n1, "
    "PARTITION ventas_f1 VALUES LESS THAN (MAXVALUE) ON n2)"
)


class Intercept(httpx.AsyncBaseTransport):
    """Pasa las peticiones al nodo real salvo las que ``fail`` decida contestar."""

    def __init__(self, app, fail=None):
        self.inner = httpx.ASGITransport(app=app)
        self.fail = fail

    async def handle_async_request(self, request):
        if self.fail is not None:
            body = json.loads(request.content) if request.content else None
            replaced = self.fail(request, body)
            if replaced is not None:
                return replaced
        return await self.inner.handle_async_request(request)


def coordinator_for(services, data_dir=None, **fails):
    transports = {n: Intercept(create_node_app(s), fails.get(n)) for n, s in services.items()}
    return Coordinator(services["n1"].catalog, transports=transports, data_dir=data_dir)


def query(client, sql):
    return client.post("/query", json={"sql": sql})


def tables_of(service):
    return sorted(service._database.table_names())


def test_crear_usar_indexar_y_borrar_una_tabla_particionada(node_services, tmp_path):
    coordinator = coordinator_for(node_services, data_dir=tmp_path)
    with TestClient(create_coordinator_app(coordinator)) as client:
        created = query(client, VENTAS)
        assert created.status_code == 200, created.text
        assert created.json()["rows"] == []
        assert client.get("/health").json()["catalog_version"] == 2

        for key in (7, 9000):
            assert query(client, f"INSERT INTO ventas VALUES ({key}, 1.5)").status_code == 200
        rows = query(client, "SELECT id FROM ventas ORDER BY id").json()["rows"]
        assert rows == [[7], [9000]]
        fragments = {t["table"]: t for t in client.get("/fragments").json()}
        assert [
            (f["fragment_id"], f["node_id"], f["lower"], f["upper"], f["record_count"])
            for f in fragments["ventas"]["fragments"]
        ] == [("ventas_f0", "n1", None, 5000, 1), ("ventas_f1", "n2", 5000, None, 1)]

        assert (
            query(client, "CREATE INDEX por_monto ON ventas (monto) USING BPLUS").status_code == 200
        )
        ventas = next(t for t in client.get("/tables").json() if t["name"] == "ventas")
        assert ventas["record_count"] == 2
        assert [i["name"] for i in ventas["indexes"]] == ["por_monto"]

        assert (
            query(
                client, "CREATE TABLE notas (id INT PRIMARY KEY, t VARCHAR(10)) ON n2"
            ).status_code
            == 200
        )
        assert query(client, "INSERT INTO notas VALUES (1, 'hola')").status_code == 200
        assert tables_of(node_services["n1"]) == ["items", "ventas"]
        assert tables_of(node_services["n2"]) == ["items", "notas", "ventas"]

        assert query(client, "DROP TABLE ventas").status_code == 200
        assert [t["name"] for t in client.get("/tables").json()] == ["items", "notas"]
        assert client.get("/health").json() | {"nodes": None} == {
            "status": "ready",
            "catalog_version": 5,
            "nodes": None,
        }
    for service in node_services.values():
        assert service.catalog.catalog_version == 5
    assert newest_catalog(node_services["n1"].catalog, tmp_path) == coordinator.catalog


@pytest.mark.parametrize(
    ("sql", "kind", "fragment"),
    [
        ("CREATE TABLE t (id INT PRIMARY KEY)", "unsupported", "ON nodo"),
        (VENTAS.replace("ON n2", "ON n9"), "semantic", "n9"),
        ("CREATE TABLE items (id INT PRIMARY KEY) ON n1", "semantic", "ya existe"),
        ("DROP TABLE nada", "semantic", "no existe"),
    ],
)
def test_un_ddl_invalido_no_llega_a_los_nodos(node_services, sql, kind, fragment):
    coordinator = coordinator_for(node_services)
    with TestClient(create_coordinator_app(coordinator)) as client:
        response = query(client, sql)
    assert response.status_code == 400
    body = response.json()
    assert body["kind"] == kind and fragment in body["error"]
    assert body["line"] == 1
    for service in node_services.values():
        assert service.catalog.catalog_version == 1


def test_si_un_nodo_rechaza_el_ddl_se_deshace_en_todos(node_services):
    attempts = []

    def reject_first(request, body):
        if request.url.path.endswith("/catalog/apply") and not attempts:
            attempts.append(body)
            return httpx.Response(
                400,
                json={
                    "protocol_version": 1,
                    "catalog_version": body["catalog_version"],
                    "node_id": "n2",
                    "request_id": body["request_id"],
                    "fragment_id": None,
                    "error": {
                        "code": "SQL_ERROR",
                        "message": "no se pudo crear",
                        "execution_state": "not_started",
                        "kind": "semantic",
                        "line": None,
                        "column": None,
                        "end_line": None,
                        "end_column": None,
                    },
                },
            )
        return None

    coordinator = coordinator_for(node_services, n2=reject_first)
    with TestClient(create_coordinator_app(coordinator)) as client:
        response = query(client, VENTAS)
        assert response.status_code == 400
        assert "se deshizo en todos los nodos" in response.json()["error"]
        assert "[SQL_ERROR] nodo n2" in response.json()["error"]
        # n1 alcanzó a crear su fragmento y lo borró al deshacer.
        assert tables_of(node_services["n1"]) == ["items"]
        assert {s.catalog.catalog_version for s in node_services.values()} == {3}
        assert coordinator.catalog.catalog_version == 3
        assert [t.name for t in coordinator.catalog.tables] == ["items"]
        assert client.get("/health").status_code == 200
        # El clúster sigue usable: el mismo DDL entra a la siguiente.
        assert query(client, VENTAS).status_code == 200
        assert coordinator.catalog.catalog_version == 4


def test_con_un_nodo_caido_no_se_cambia_nada(node_services):
    def down(request, body):
        raise httpx.ConnectError("caído", request=request)

    coordinator = coordinator_for(node_services, n2=down)
    with TestClient(create_coordinator_app(coordinator)) as client:
        response = query(client, VENTAS)
    assert response.status_code == 503
    assert "todos los nodos listos" in response.json()["error"]
    assert tables_of(node_services["n1"]) == ["items"]
    assert node_services["n1"].catalog.catalog_version == 1


def test_dos_ddl_simultaneos_se_ordenan(node_services):
    coordinator = coordinator_for(node_services)

    async def both():
        try:
            return await asyncio.gather(
                coordinator.query("CREATE TABLE a (id INT PRIMARY KEY) ON n1"),
                coordinator.query("CREATE TABLE b (id INT PRIMARY KEY) ON n2"),
            )
        finally:
            await coordinator.aclose()

    asyncio.run(both())
    assert coordinator.catalog.catalog_version == 3
    assert sorted(t.name for t in coordinator.catalog.tables) == ["a", "b", "items"]
