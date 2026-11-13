"""``POST /query`` del coordinador (#279): enrutamiento, combinación y fallos.

Primero con nodos falsos, para controlar qué responde cada uno; después contra
nodos reales del #277 en memoria, comparando con el motor local.
"""

import asyncio
import copy

import httpx
import pytest
from fastapi.testclient import TestClient

from engine.distributed.catalog import DistributedCatalog
from engine.distributed.coordinator import Coordinator, create_coordinator_app
from engine.distributed.coordinator_test_support import FakeNode, error_body
from engine.distributed.router import double_sql

ERROR_KEYS = {"error", "kind", "line", "column", "end_line", "end_column"}


def run(coro):
    return asyncio.run(coro)


def fakes(catalog, rows=None, **overrides) -> dict[str, FakeNode]:
    nodes = {n.node_id: FakeNode(catalog, n.node_id, rows=rows) for n in catalog.nodes}
    for node_id, override in overrides.items():
        nodes[node_id].override = override
    return nodes


def api(catalog, nodes) -> TestClient:
    coordinator = Coordinator(catalog, transports={n: f.transport for n, f in nodes.items()})
    return TestClient(create_coordinator_app(coordinator))


def fragment_calls(node: FakeNode) -> list[tuple[str, dict]]:
    """``(ruta, cuerpo)`` de las peticiones a fragmentos, sin las de salud."""

    return [
        (request.url.path.split("/internal/v1/")[1], body)
        for request, body in node.requests
        if "/fragments/" in request.url.path
    ]


ROWS = {
    "items_f0": [[3, "c"], [9999, "z"], [1, "a"]],
    "items_f1": [[10000, "k"], [20000, "m"]],
}


# -- con nodos falsos ------------------------------------------------------------


def test_busqueda_por_clave_contacta_solo_al_dueno(node_catalog):
    nodes = fakes(node_catalog, ROWS)
    with api(node_catalog, nodes) as client:
        response = client.post("/query", json={"sql": "SELECT nombre FROM items WHERE id = 20000"})
    assert response.status_code == 200
    assert fragment_calls(nodes["n1"]) == []
    ((path, body),) = fragment_calls(nodes["n2"])
    assert path == "fragments/items_f1/scan"
    assert body["sql"] == "SELECT * FROM items WHERE id = 20000;"


def test_lectura_multinodo_combinada(node_catalog):
    nodes = fakes(node_catalog, ROWS)
    with api(node_catalog, nodes) as client:
        response = client.post(
            "/query", json={"sql": "SELECT nombre, id FROM items ORDER BY id DESC LIMIT 4"}
        )
    assert response.status_code == 200
    assert response.json() == {
        "columns": ["nombre", "id"],
        "column_types": ["VARCHAR", "INT"],
        "rows": [["m", 20000], ["k", 10000], ["z", 9999], ["c", 3]],
        "affected_rows": 0,
        "plan": None,
        "spatial_context": None,
        "is_explain": False,
    }
    assert len(fragment_calls(nodes["n1"])) == len(fragment_calls(nodes["n2"])) == 1


def test_lectura_sin_fragmentos_no_contacta_nodos(node_catalog):
    nodes = fakes(node_catalog, ROWS)
    with api(node_catalog, nodes) as client:
        response = client.post("/query", json={"sql": "SELECT * FROM items WHERE id > 2147483647"})
    assert response.status_code == 200
    assert response.json()["rows"] == []
    assert response.json()["column_types"] == ["INT", "VARCHAR"]
    assert fragment_calls(nodes["n1"]) == fragment_calls(nodes["n2"]) == []


def test_un_nodo_caido_hace_fallar_toda_la_lectura(node_catalog):
    def down(request, body):
        raise httpx.ConnectError("rechazada", request=request)

    with api(node_catalog, fakes(node_catalog, ROWS, n2=down)) as client:
        response = client.post("/query", json={"sql": "SELECT * FROM items"})
    assert response.status_code == 503
    body = response.json()
    assert set(body) == ERROR_KEYS
    assert body["error"].startswith("[NODE_UNAVAILABLE] nodo n2, fragmento items_f1:")
    assert "rows" not in body


def test_un_error_del_scan_no_apunta_al_sql_del_usuario(node_catalog):
    def roto(request, body):
        if request.url.path.endswith("/scan"):
            data = error_body(
                nodes["n1"], body, "SQL_ERROR", "not_started", kind="semantic", line=1, column=27
            )
            return httpx.Response(400, json=data)
        return None

    nodes = fakes(node_catalog, ROWS, n1=roto)
    with api(node_catalog, nodes) as client:
        response = client.post("/query", json={"sql": "SELECT * FROM items WHERE nombre = 'x'"})
    assert response.status_code == 400
    body = response.json()
    assert body["error"] == "[SQL_ERROR] nodo n1, fragmento items_f0: falla SQL_ERROR"
    assert body["kind"] == "semantic"
    assert (body["line"], body["column"], body["end_line"], body["end_column"]) == (None,) * 4


def test_un_error_de_escritura_conserva_su_ubicacion(node_catalog):
    def duplicado(request, body):
        if request.url.path.endswith("/execute"):
            data = error_body(nodes["n2"], body, "DUPLICATE_KEY", "unknown", line=1, column=1)
            return httpx.Response(409, json=data)
        return None

    nodes = fakes(node_catalog, n2=duplicado)
    with api(node_catalog, nodes) as client:
        response = client.post("/query", json={"sql": "INSERT INTO items VALUES (10000, 'x')"})
    assert response.status_code == 409
    assert (response.json()["line"], response.json()["column"]) == (1, 1)


def test_insert_y_delete_van_al_dueno_con_el_sql_original(node_catalog):
    nodes = fakes(node_catalog)
    with api(node_catalog, nodes) as client:
        insert = client.post("/query", json={"sql": "INSERT INTO items VALUES (9999, 'x')"})
        delete = client.post("/query", json={"sql": "DELETE FROM items WHERE id = 10000"})
    assert insert.json()["affected_rows"] == delete.json()["affected_rows"] == 1
    assert fragment_calls(nodes["n1"])[0][0] == "fragments/items_f0/execute"
    assert fragment_calls(nodes["n1"])[0][1]["sql"] == "INSERT INTO items VALUES (9999, 'x')"
    assert fragment_calls(nodes["n2"])[0][0] == "fragments/items_f1/execute"


def test_escritura_sin_respuesta_es_incierta(node_catalog):
    def lento(request, body):
        if request.url.path.endswith("/execute"):
            raise httpx.ReadTimeout("sin respuesta", request=request)

    with api(node_catalog, fakes(node_catalog, n1=lento)) as client:
        response = client.post("/query", json={"sql": "INSERT INTO items VALUES (1, 'x')"})
    assert response.status_code == 504
    assert "resultado de escritura incierto; no se reintentó" in response.json()["error"]


def test_una_fila_fuera_de_rango_es_respuesta_invalida(node_catalog):
    rows = {"items_f0": [[10000, "no es mía"]], "items_f1": []}
    with api(node_catalog, fakes(node_catalog, rows)) as client:
        response = client.post("/query", json={"sql": "SELECT * FROM items"})
    assert response.status_code == 502
    assert response.json()["error"].startswith(
        "[INVALID_NODE_RESPONSE] nodo n1, fragmento items_f0"
    )


@pytest.mark.parametrize(
    ("sql", "kind", "column"),
    [
        ("SELECT * FROM items ORDER BY nombre", "unsupported", 21),
        ("SELECT * FROM nada", "semantic", 15),
        ("SELECT FROM items", "parse", 8),
    ],
)
def test_errores_del_coordinador_con_la_forma_de_la_api_local(node_catalog, sql, kind, column):
    nodes = fakes(node_catalog, ROWS)
    with api(node_catalog, nodes) as client:
        response = client.post("/query", json={"sql": sql})
    assert response.status_code == 400
    body = response.json()
    assert set(body) == ERROR_KEYS
    assert (body["kind"], body["line"], body["column"]) == (kind, 1, column)
    assert fragment_calls(nodes["n1"]) == fragment_calls(nodes["n2"]) == []


def test_single_devuelve_la_respuesta_del_dueno_sin_tocarla(catalog_data):
    data = copy.deepcopy(catalog_data)
    data["tables"][0].update(
        mode="single",
        fragments=[{"fragment_id": "items_f0", "node_id": "n2", "lower": None, "upper": None}],
    )
    catalog = DistributedCatalog.model_validate(data)
    plan = {"operator": "SeqScan", "children": []}

    def con_plan(request, body):
        if request.url.path.endswith("/execute"):
            result = {
                "columns": ["nombre"],
                "column_types": ["VARCHAR"],
                "rows": [["b"], ["a"]],
                "affected_rows": 0,
                "plan": plan,
                "spatial_context": None,
                "is_explain": False,
            }
            return httpx.Response(
                200,
                json=nodes["n2"].base(
                    request_id=body["request_id"], fragment_id="items_f0", result=result
                ),
            )
        return None

    nodes = fakes(catalog, n2=con_plan)
    sql = "SELECT nombre FROM items ORDER BY nombre DESC"
    with api(catalog, nodes) as client:
        response = client.post("/query", json={"sql": sql})
    assert response.status_code == 200
    assert response.json()["rows"] == [["b"], ["a"]]
    assert response.json()["plan"] == plan
    assert fragment_calls(nodes["n2"])[0][1]["sql"] == sql


# -- contra nodos reales y el motor local ---------------------------------------

COLUMNS = [
    {"name": "precio", "type": "DOUBLE", "size": None},
    {"name": "fecha", "type": "DATE", "size": None},
    {"name": "activo", "type": "BOOL", "size": None},
]

FILAS = [
    (-2147483648, "minimo", -1.5, "2026-01-01", True),
    (-3, "negativo", 0.25, "2026-03-15", False),
    (5, "quipu", 10.0, "2026-05-05", True),
    (9999, "frontera", 10.0, "2026-05-06", False),
    (10000, "quipu", 7.75, "2026-07-07", True),
    (15000, "o'brien", 0.00001, "2026-12-31", True),
    (2147483647, "maximo", 99.5, "2027-01-01", False),
]


def insert_sql(row) -> str:
    key, nombre, precio, fecha, activo = row
    nombre = nombre.replace("'", "''")
    return (
        f"INSERT INTO items VALUES ({key}, '{nombre}', {double_sql(precio)}, DATE '{fecha}', "
        f"{'TRUE' if activo else 'FALSE'})"
    )


@pytest.fixture
def clusters(tmp_path, catalog_data):
    """Dos nodos reales en memoria y una base local con el mismo esquema."""

    native = pytest.importorskip("quipudb_native")
    from engine.distributed.fragments import FragmentService
    from engine.distributed.node_api import create_node_app
    from engine.distributed.storage import prepare_range_node
    from engine.executor import QueryProcessor
    from engine.executor.native import to_native_schema

    data = copy.deepcopy(catalog_data)
    data["tables"][0]["columns"].extend(COLUMNS)
    catalog = DistributedCatalog.model_validate(data)
    services = {}
    local_dir = tmp_path / "local"
    local_dir.mkdir()
    database = native.Database(local_dir / "catalogo.txt")
    table = catalog.table("items")
    database.create_table(to_native_schema(table.bound_schema, native), table.storage)
    for index in table.indexes:
        database.create_index(table.name, index.name, index.column, index.structure)
    try:
        for node in ("n1", "n2"):
            prepare_range_node(catalog, node, tmp_path / node)
            services[node] = FragmentService(catalog, node, tmp_path / node)
            services[node].start()
        coordinator = Coordinator(
            catalog,
            transports={
                n: httpx.ASGITransport(app=create_node_app(s)) for n, s in services.items()
            },
        )
        yield coordinator, QueryProcessor(database), services
        run(coordinator.aclose())
    finally:
        for service in services.values():
            service.close()
        database.flush()


CONSULTAS = [
    "SELECT * FROM items ORDER BY id",
    "SELECT * FROM items ORDER BY id DESC",
    "SELECT nombre, id FROM items ORDER BY id DESC LIMIT 3",
    "SELECT items.nombre FROM items WHERE id = 9999",
    "SELECT * FROM items WHERE id = 10000",
    "SELECT * FROM items WHERE id = 42",
    "SELECT id FROM items WHERE id >= 9999 ORDER BY id",
    "SELECT id FROM items WHERE id < 10000 ORDER BY id DESC LIMIT 2",
    "SELECT id FROM items WHERE id <= -2147483648 ORDER BY id",
    "SELECT id FROM items WHERE id > 2147483646 ORDER BY id",
    "SELECT id, nombre FROM items WHERE nombre = 'quipu' ORDER BY id",
    "SELECT id FROM items WHERE nombre = 'o''brien' ORDER BY id",
    "SELECT id, precio FROM items WHERE precio > 0.000001 ORDER BY id",
    "SELECT id FROM items WHERE precio = 10 ORDER BY id",
    "SELECT id, fecha FROM items WHERE fecha >= DATE '2026-05-06' ORDER BY id",
    "SELECT id FROM items WHERE activo = FALSE ORDER BY id DESC",
    "SELECT nombre FROM items WHERE id > 0 ORDER BY id LIMIT 0",
    "SELECT id, id FROM items WHERE id = 5",
]


def local_json(processor, sql) -> dict:
    from engine.api.service import to_response

    return to_response(processor.execute(sql)).model_dump(mode="json")


def test_mismo_resultado_que_el_motor_local(clusters):
    coordinator, local, _ = clusters
    for row in FILAS:
        run(coordinator.query(insert_sql(row)))
        local.execute(insert_sql(row))
    for sql in CONSULTAS:
        distributed = run(coordinator.query(sql)).model_dump(mode="json")
        expected = local_json(local, sql)
        for field in ("columns", "column_types", "rows"):
            assert distributed[field] == expected[field], (sql, field)
        assert distributed["plan"] is None


def test_cada_fila_queda_en_su_nodo_y_se_borra_por_clave(clusters):
    coordinator, _, services = clusters
    for row in FILAS:
        assert run(coordinator.query(insert_sql(row))).affected_rows == 1

    def keys(node, fragment):
        from engine.distributed.node_test_support import scan

        return sorted(r[0] for r in scan(services[node], fragment=fragment).rows)

    assert keys("n1", "items_f0") == [-2147483648, -3, 5, 9999]
    assert keys("n2", "items_f1") == [10000, 15000, 2147483647]

    assert run(coordinator.query("DELETE FROM items WHERE id = 10000")).affected_rows == 1
    assert run(coordinator.query("DELETE FROM items WHERE id = 10000")).affected_rows == 0
    assert keys("n2", "items_f1") == [15000, 2147483647]
    assert run(coordinator.query("SELECT * FROM items WHERE id = 10000")).rows == []


def test_clave_duplicada_llega_como_error_del_nodo(clusters):
    from engine.distributed.client import NodeFailure

    coordinator, _, _ = clusters
    run(coordinator.query(insert_sql(FILAS[4])))
    with pytest.raises(NodeFailure) as caught:
        run(coordinator.query(insert_sql(FILAS[4])))
    assert (caught.value.code, caught.value.node_id, caught.value.fragment_id) == (
        "DUPLICATE_KEY",
        "n2",
        "items_f1",
    )


def test_tabla_vacia_conserva_columnas_y_tipos(clusters):
    coordinator, local, _ = clusters
    sql = "SELECT fecha, nombre FROM items ORDER BY id"
    distributed = run(coordinator.query(sql)).model_dump(mode="json")
    assert distributed["rows"] == []
    assert distributed["column_types"] == local_json(local, sql)["column_types"]
