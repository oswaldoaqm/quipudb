"""Arranque, exclusión del directorio, auditoría y cierre del motor real."""

import pytest
from fastapi.testclient import TestClient

from engine.distributed.fragments import FragmentService
from engine.distributed.node_api import create_node_app
from engine.distributed.node_errors import NodeError
from engine.distributed.node_test_support import envelope, insert, scan
from engine.distributed.storage import prepare_range_node
from engine.executor import QueryProcessor


def test_directorio_ocupado_y_liberacion(node_services, node_catalog):
    original = node_services["n1"]
    second = FragmentService(node_catalog, "n1", original.directory)
    try:
        second.start()
        assert second.health().status == "not_ready"
        with pytest.raises(NodeError) as caught:
            second.describe(envelope())
        assert caught.value.code == "NODE_NOT_READY"
        insert(original, 1)
        original.close()
        second.start()
        assert second.health().status == "ready"
        assert scan(second).rows == [[1, "fila"]]
    finally:
        second.close()


def test_catalogo_ausente_no_se_crea(tmp_path, node_catalog):
    service = FragmentService(node_catalog, "n1", tmp_path / "missing")
    with TestClient(create_node_app(service)) as client:
        assert client.get("/internal/v1/health").status_code == 503
        error = client.post("/internal/v1/catalog", json=envelope())
        assert error.status_code == 409 and error.json()["error"]["code"] == "SCHEMA_MISMATCH"
    assert not (tmp_path / "missing").exists()


def test_identidad_inexistente_no_arranca(tmp_path, node_catalog):
    with pytest.raises(KeyError):
        FragmentService(node_catalog, "otro", tmp_path)


def test_indice_alterado_inhabilita_servicio(node_services):
    service = node_services["n1"]
    service._database.drop_index("items", "por_nombre")
    with pytest.raises(NodeError) as caught:
        insert(service, 1)
    assert caught.value.code == "SCHEMA_MISMATCH"
    assert service.health().status == "not_ready"
    assert service._database.table("items").size() == 0


def test_arranque_detecta_filas_fuera_del_fragmento(tmp_path, node_catalog):
    native = pytest.importorskip("quipudb_native")
    directory = tmp_path / "node"
    path = prepare_range_node(node_catalog, "n1", directory)
    db = native.Database(path)
    QueryProcessor(db).execute("INSERT INTO items VALUES (10000, 'mal')")
    db.flush()
    db.close("items")
    del db
    service = FragmentService(node_catalog, "n1", directory)
    try:
        service.start()
        assert service.health().status == "not_ready"
        assert "fuera del fragmento" in service.health().reason
    finally:
        service.close()
