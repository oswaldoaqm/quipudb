"""Coordinador contra dos nodos que son procesos Uvicorn reales, por TCP (#278).

Las demás pruebas del coordinador usan transporte en memoria. Esta comprueba lo
mismo con la red de verdad: puertos, conexión rechazada al caer un nodo y
timeouts del cliente.
"""

import asyncio
import json

import httpx
import pytest

from engine.distributed.catalog import load_catalog
from engine.distributed.client import NodeFailure
from engine.distributed.coordinator import Coordinator
from engine.distributed.node_process_support import RunningNode, free_port
from engine.distributed.storage import prepare_range_node


@pytest.fixture
def cluster(tmp_path, catalog_data):
    pytest.importorskip("quipudb_native")
    ports = [free_port(), free_port()]
    while ports[0] == ports[1]:
        ports[1] = free_port()
    for node, port in zip(catalog_data["nodes"], ports, strict=True):
        node["base_url"] = f"http://127.0.0.1:{port}"
    config = tmp_path / "cluster.json"
    config.write_text(json.dumps(catalog_data))
    catalog = load_catalog(config, expected_catalog_version=1)
    nodes = {}
    try:
        for node_id, port in zip(("n1", "n2"), ports, strict=True):
            directory = tmp_path / node_id
            prepare_range_node(catalog, node_id, directory)
            nodes[node_id] = RunningNode(config, node_id, directory, port)
            nodes[node_id].start()
        yield nodes, catalog
    finally:
        for node in nodes.values():
            node.close()


def test_coordinador_por_tcp_con_dos_procesos(cluster):
    nodes, catalog = cluster

    async def recorrido():
        coordinator = Coordinator(catalog, timeout=httpx.Timeout(5.0, connect=1.0, pool=1.0))
        try:
            antes = await coordinator.check_nodes()
            for key, fragment, node in ((1, "items_f0", "n1"), (20000, "items_f1", "n2")):
                await coordinator.client(node).execute(
                    fragment, "items", f"INSERT INTO items VALUES ({key}, 'x')", write=True
                )
            tablas = await coordinator.tables()

            nodes["n2"].close()  # se cae un nodo
            despues = await coordinator.check_nodes()
            with pytest.raises(NodeFailure) as caido:
                await coordinator.tables()
            # El otro nodo sigue respondiendo por su cuenta.
            filas = await coordinator.client("n1").scan("items_f0", "items", "SELECT * FROM items")
            return antes, tablas, despues, caido.value, filas
        finally:
            await coordinator.aclose()

    antes, tablas, despues, caido, filas = asyncio.run(recorrido())
    assert [(s.node_id, s.ready) for s in antes] == [("n1", True), ("n2", True)]
    assert [(t.name, t.record_count) for t in tablas] == [("items", 2)]
    assert [(s.node_id, s.ready) for s in despues] == [("n1", True), ("n2", False)]
    assert despues[1].reason.startswith("[NODE_UNAVAILABLE] nodo n2")
    assert (caido.code, caido.status, caido.execution_state) == (
        "NODE_UNAVAILABLE",
        503,
        "not_started",
    )
    assert filas.result.rows == [[1, "x"]]
