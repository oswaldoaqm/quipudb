"""Coordinador (#278): revisión de nodos, vista lógica de tablas y API pública."""

import asyncio
import copy

import httpx
import pytest
from fastapi.testclient import TestClient

from engine.distributed.catalog import DistributedCatalog
from engine.distributed.client import NodeFailure
from engine.distributed.coordinator import Coordinator, create_coordinator_app
from engine.distributed.coordinator_test_support import FakeNode, error_body
from engine.distributed.node_test_support import envelope


def run(coro):
    return asyncio.run(coro)


def coordinator_with(catalog, nodes: dict[str, FakeNode]) -> Coordinator:
    return Coordinator(catalog, transports={n: node.transport for n, node in nodes.items()})


async def using(coordinator: Coordinator, coro_factory):
    try:
        return await coro_factory(coordinator)
    finally:
        await coordinator.aclose()


def fakes(catalog, **changes) -> dict[str, FakeNode]:
    nodes = {n.node_id: FakeNode(catalog, n.node_id) for n in catalog.nodes}
    for node_id, override in changes.items():
        nodes[node_id].override = override
    return nodes


def down(request, body):
    raise httpx.ConnectError("rechazada", request=request)


# -- revisión de nodos ----------------------------------------------------------


def test_registra_un_cliente_por_nodo_del_catalogo(node_catalog):
    coordinator = Coordinator(node_catalog)
    assert sorted(coordinator.clients) == ["n1", "n2"]
    assert coordinator.client("n2").node.base_url == "http://n2:8000"
    run(coordinator.aclose())


def test_dos_nodos_correctos_quedan_listos(node_catalog):
    statuses = run(
        using(coordinator_with(node_catalog, fakes(node_catalog)), lambda c: c.check_nodes())
    )
    assert [(s.node_id, s.ready, s.reason, s.fragment_ids) for s in statuses] == [
        ("n1", True, None, ("items_f0",)),
        ("n2", True, None, ("items_f1",)),
    ]


def test_un_nodo_caido_es_un_estado_no_una_excepcion(node_catalog):
    statuses = run(
        using(
            coordinator_with(node_catalog, fakes(node_catalog, n2=down)), lambda c: c.check_nodes()
        )
    )
    n1, n2 = statuses
    assert n1.ready
    assert not n2.ready
    assert n2.reason.startswith("[NODE_UNAVAILABLE] nodo n2")


def test_un_nodo_no_listo_informa_su_motivo(node_catalog):
    def no_listo(request, body):
        if request.url.path.endswith("/health"):
            return httpx.Response(
                503,
                json=nodes["n1"].base(
                    status="not_ready", fragment_ids=[], reason="falta el catálogo"
                ),
            )
        return None

    nodes = fakes(node_catalog, n1=no_listo)
    n1, _ = run(using(coordinator_with(node_catalog, nodes), lambda c: c.check_nodes()))
    assert (n1.ready, n1.reason) == (False, "falta el catálogo")


def test_un_nodo_con_otros_fragmentos_no_queda_listo(node_catalog):
    def otro_fragmento(request, body):
        if request.url.path.endswith("/health"):
            return httpx.Response(
                200, json=nodes["n1"].base(status="ready", fragment_ids=["items_f1"], reason=None)
            )
        return None

    nodes = fakes(node_catalog, n1=otro_fragmento)
    n1, _ = run(using(coordinator_with(node_catalog, nodes), lambda c: c.check_nodes()))
    assert not n1.ready
    assert "aloja ['items_f1']" in n1.reason


def test_un_esquema_distinto_no_queda_listo(node_catalog):
    def otra_columna(request, body):
        if request.url.path.endswith("/catalog"):
            data = FakeNode(node_catalog, "n2").handle(request).json()
            data["fragments"][0]["table_info"]["columns"][1]["size"] = 10
            return httpx.Response(200, json=data)
        return None

    nodes = fakes(node_catalog, n2=otra_columna)
    _, n2 = run(using(coordinator_with(node_catalog, nodes), lambda c: c.check_nodes()))
    assert not n2.ready
    assert n2.reason == "fragmento items_f1: las columnas de items no coinciden con el catálogo"


# -- GET /tables ----------------------------------------------------------------


def test_una_tabla_range_aparece_una_vez_con_los_conteos_sumados(node_catalog):
    nodes = fakes(node_catalog)
    nodes["n1"].counts = {"items_f0": 2}
    nodes["n2"].counts = {"items_f1": 3}
    tables = run(using(coordinator_with(node_catalog, nodes), lambda c: c.tables()))
    assert [(t.name, t.record_count) for t in tables] == [("items", 5)]
    assert [c.name for c in tables[0].columns] == ["id", "nombre"]


def test_una_tabla_single_conserva_la_descripcion_de_su_dueno(catalog_data):
    data = copy.deepcopy(catalog_data)
    data["tables"].append(
        {
            "name": "notas",
            "mode": "single",
            "storage": "sequential",
            "primary_key": "codigo",
            "columns": [
                {"name": "codigo", "type": "VARCHAR", "size": 8},
                {"name": "texto", "type": "VARCHAR", "size": 40},
            ],
            "indexes": [],
            "fragments": [
                {"fragment_id": "notas_f0", "node_id": "n2", "lower": None, "upper": None}
            ],
        }
    )
    catalog = DistributedCatalog.model_validate(data)
    nodes = fakes(catalog)
    nodes["n2"].counts = {"items_f1": 1, "notas_f0": 4}
    tables = run(using(coordinator_with(catalog, nodes), lambda c: c.tables()))
    notas = next(t for t in tables if t.name == "notas")
    assert (notas.storage, notas.record_count) == ("sequential", 4)
    assert next(t for t in tables if t.name == "items").record_count == 1


def test_sin_un_nodo_no_hay_conteo_parcial(node_catalog):
    with pytest.raises(NodeFailure) as caught:
        run(
            using(
                coordinator_with(node_catalog, fakes(node_catalog, n2=down)), lambda c: c.tables()
            )
        )
    assert (caught.value.code, caught.value.status) == ("NODE_UNAVAILABLE", 503)


def test_tables_rechaza_un_nodo_con_otro_esquema(node_catalog):
    def otro_indice(request, body):
        if request.url.path.endswith("/catalog"):
            data = FakeNode(node_catalog, "n1").handle(request).json()
            data["fragments"][0]["table_info"]["indexes"] = []
            return httpx.Response(200, json=data)
        return None

    with pytest.raises(NodeFailure) as caught:
        run(
            using(
                coordinator_with(node_catalog, fakes(node_catalog, n1=otro_indice)),
                lambda c: c.tables(),
            )
        )
    assert (caught.value.code, caught.value.status) == ("SCHEMA_MISMATCH", 409)
    assert "los índices de items no coinciden" in caught.value.message


def test_api_publica_health_y_tables(node_catalog):
    nodes = fakes(node_catalog)
    nodes["n1"].counts = {"items_f0": 1}
    app = create_coordinator_app(coordinator_with(node_catalog, nodes))
    with TestClient(app) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ready"
        assert [n["node_id"] for n in health.json()["nodes"]] == ["n1", "n2"]
        tables = client.get("/tables", headers={"Origin": "http://localhost:5173"})
        assert tables.status_code == 200
        assert tables.json()[0]["record_count"] == 1
        assert tables.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_api_publica_con_un_nodo_caido(node_catalog):
    app = create_coordinator_app(coordinator_with(node_catalog, fakes(node_catalog, n2=down)))
    with TestClient(app) as client:
        health = client.get("/health")
        assert health.status_code == 503
        assert health.json()["status"] == "degraded"
        tables = client.get("/tables")
        assert tables.status_code == 503
        body = tables.json()
        assert body["error"].startswith("[NODE_UNAVAILABLE] nodo n2:")
        assert set(body) == {"error", "kind", "line", "column", "end_line", "end_column"}


def test_api_publica_traduce_errores_del_nodo(node_catalog):
    def roto(request, body):
        if request.url.path.endswith("/catalog"):
            return httpx.Response(503, json=error_body(nodes["n1"], body, "NODE_NOT_READY"))
        return None

    nodes = fakes(node_catalog, n1=roto)
    with TestClient(create_coordinator_app(coordinator_with(node_catalog, nodes))) as client:
        response = client.get("/tables")
    assert response.status_code == 503
    assert response.json()["error"] == "[NODE_NOT_READY] nodo n1: falla NODE_NOT_READY"


# -- contra nodos reales del #277 ----------------------------------------------


def real_coordinator(node_catalog, node_services) -> Coordinator:
    from engine.distributed.node_api import create_node_app

    return Coordinator(
        node_catalog,
        transports={
            n: httpx.ASGITransport(app=create_node_app(s)) for n, s in node_services.items()
        },
    )


def test_coordinador_con_dos_nodos_reales(node_catalog, node_services):
    async def recorrido(c: Coordinator):
        statuses = await c.check_nodes()
        for key, fragment, node in (
            (5, "items_f0", "n1"),
            (9999, "items_f0", "n1"),
            (10000, "items_f1", "n2"),
        ):
            await c.client(node).execute(
                fragment, "items", f"INSERT INTO items VALUES ({key}, 'k{key}')", write=True
            )
        return statuses, await c.tables()

    statuses, tables = run(using(real_coordinator(node_catalog, node_services), recorrido))
    assert all(s.ready for s in statuses)
    assert [(t.name, t.record_count) for t in tables] == [("items", 3)]
    # Cada nodo guardó solo lo suyo.
    (local,) = node_services["n1"].describe(envelope()).fragments
    assert local.table_info.record_count == 2
