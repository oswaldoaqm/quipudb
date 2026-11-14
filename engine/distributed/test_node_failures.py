"""Fallo inducido después de una mutación real: no prometer rollback ni reintentar."""

from fastapi.testclient import TestClient

from engine.distributed.node_api import create_node_app
from engine.distributed.node_test_support import envelope, scan
from engine.executor import processor


def test_escritura_incierta_y_reinicio(node_services, monkeypatch):
    service = node_services["n1"]
    original = processor.insert_with_indexes

    def fail_after_write(*args, **kwargs):
        original(*args, **kwargs)
        raise OSError("fallo inducido después de escribir")

    with TestClient(create_node_app(service)) as client:
        monkeypatch.setattr(processor, "insert_with_indexes", fail_after_write)
        result = client.post(
            "/internal/v1/fragments/items_f0/execute",
            json=envelope("INSERT INTO items VALUES (1, 'x')"),
        )
        assert result.status_code == 500
        assert result.json()["error"]["execution_state"] == "unknown"
        assert result.json()["error"]["code"] == "EXECUTION_ERROR"
        assert client.get("/internal/v1/health").status_code == 503
        assert service._database.table("items").size() == 1
    monkeypatch.setattr(processor, "insert_with_indexes", original)
    service.start()
    assert service.health().status == "ready"
    assert scan(service).rows == [[1, "x"]]
