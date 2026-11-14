"""Dos servidores Uvicorn + C++ reales, TCP, aislamiento y reinicios."""

import hashlib
import json

import pytest

from engine.distributed.catalog import load_catalog
from engine.distributed.node_process_support import RunningNode, free_port
from engine.distributed.node_test_support import envelope
from engine.distributed.storage import prepare_range_node, verify_range_node


@pytest.fixture
def processes(tmp_path, catalog_data):
    pytest.importorskip("quipudb_native")
    ports = [free_port(), free_port()]
    while ports[0] == ports[1]:
        ports[1] = free_port()
    for node, port in zip(catalog_data["nodes"], ports, strict=True):
        node["base_url"] = f"http://127.0.0.1:{port}"
    config = tmp_path / "cluster.json"
    config.write_text(json.dumps(catalog_data))
    catalog = load_catalog(config)
    nodes = []
    try:
        for node_id, port in zip(("n1", "n2"), ports, strict=True):
            directory = tmp_path / node_id
            prepare_range_node(catalog, node_id, directory)
            node = RunningNode(config, node_id, directory, port)
            nodes.append(node)
            node.start()
        yield nodes, catalog
    finally:
        for node in nodes:
            node.close()


def _read(node, fragment):
    status, result = node.request(f"/fragments/{fragment}/scan", envelope("SELECT * FROM items"))
    assert status == 200, result
    return sorted(row[0] for row in result["result"]["rows"])


def test_operaciones_http_aislamiento_y_persistencia(processes):
    nodes, catalog = processes
    first, second = nodes
    pids = [node.process.pid for node in nodes]
    assert len(set(pids)) == 2
    for key, node, fragment in [
        (-2147483648, first, "items_f0"),
        (9999, first, "items_f0"),
        (10000, second, "items_f1"),
        (2147483647, second, "items_f1"),
    ]:
        status, result = node.request(
            f"/fragments/{fragment}/execute", envelope(f"INSERT INTO items VALUES ({key}, 'fila')")
        )
        assert status == 200 and result["result"]["affected_rows"] == 1
    assert _read(first, "items_f0") == [-2147483648, 9999]
    assert _read(second, "items_f1") == [10000, 2147483647]
    before = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in second.directory.iterdir()
        if p.is_file()
    }
    for sql in ("INSERT INTO items VALUES (10000,'mal')", "DELETE FROM items WHERE id=10000"):
        status, result = first.request("/fragments/items_f0/execute", envelope(sql))
        assert status == 409 and result["error"]["code"] == "WRONG_FRAGMENT"
    status, result = first.request(
        "/fragments/items_f0/execute", envelope("DELETE FROM items WHERE id=9999")
    )
    assert status == 200 and result["result"]["affected_rows"] == 1
    assert before == {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in second.directory.iterdir()
        if p.is_file()
    }
    for node in nodes:
        node.close()
    assert verify_range_node(catalog, "n1", first.directory) == {"items_f0": 1}
    assert verify_range_node(catalog, "n2", second.directory) == {"items_f1": 2}
    for node in nodes:
        node.start()
    assert all(node.process.pid not in pids for node in nodes)
    assert _read(first, "items_f0") == [-2147483648]
    assert _read(second, "items_f1") == [10000, 2147483647]
    for name in ("catalogo.txt", "items.heap", "items.por_nombre.extendible_hash"):
        assert not (first.directory / name).samefile(second.directory / name)


def test_dos_servidores_no_comparten_un_directorio(processes):
    nodes, _ = processes
    first = nodes[0]
    contender = RunningNode(first.config, "n1", first.directory, free_port(), ready=False)
    try:
        health = contender.start()
        assert health["status"] == "not_ready"
        status, body = contender.request("/catalog", envelope())
        assert status == 503 and body["error"]["code"] == "NODE_NOT_READY"
        assert first.request("/health")[0] == 200
    finally:
        contender.close()
