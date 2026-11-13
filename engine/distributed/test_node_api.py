"""Contrato HTTP servido por FastAPI sobre Database real, no motores simulados."""

import pytest
from fastapi.testclient import TestClient

from engine.distributed.node_api import create_node_app
from engine.distributed.node_test_support import envelope
from engine.distributed.protocol import (
    CatalogResponse,
    ErrorResponse,
    ExecutionResponse,
    HealthResponse,
)

PREFIX = "/internal/v1"


def test_salud_catalogo_y_ciclo_http(node_services):
    with TestClient(create_node_app(node_services["n1"])) as client:
        health = client.get(f"{PREFIX}/health")
        assert health.status_code == 200
        assert HealthResponse.model_validate(health.json()).node_id == "n1"
        cat = client.post(f"{PREFIX}/catalog", json=envelope())
        assert cat.status_code == 200
        parsed = CatalogResponse.model_validate(cat.json())
        assert len(parsed.fragments) == 1
        assert parsed.fragments[0].table_info.record_count == 0
        request = envelope("INSERT INTO items VALUES (5, 'hola')")
        response = client.post(f"{PREFIX}/fragments/items_f0/execute", json=request)
        assert response.status_code == 200
        parsed = ExecutionResponse.model_validate(response.json())
        assert parsed.request_id == request["request_id"]
        assert parsed.result.affected_rows == 1
        cat = client.post(f"{PREFIX}/catalog", json=envelope()).json()
        assert cat["fragments"][0]["table_info"]["record_count"] == 1
        result = client.post(
            f"{PREFIX}/fragments/items_f0/scan", json=envelope("SELECT * FROM items WHERE id=5")
        )
        assert result.status_code == 200
        assert result.json()["result"]["rows"] == [[5, "hola"]]
        deleted = client.post(
            f"{PREFIX}/fragments/items_f0/execute", json=envelope("DELETE FROM items WHERE id=5")
        )
        assert deleted.json()["result"]["affected_rows"] == 1
        assert client.post("/query", json={"sql": "DROP TABLE items"}).status_code == 404


@pytest.mark.parametrize(
    "changes,fragment,code,status",
    [
        ({"protocol_version": 2}, "items_f0", "PROTOCOL_VERSION_MISMATCH", 409),
        ({"catalog_version": 2}, "items_f0", "CATALOG_VERSION_MISMATCH", 409),
        ({"node_id": "n2"}, "items_f0", "INVALID_REQUEST", 422),
        ({"schema": {}}, "items_f0", "INVALID_REQUEST", 422),
        ({"fragment_id": "items_f1"}, "items_f0", "INVALID_REQUEST", 422),
        ({"request_id": "bad"}, "items_f0", "INVALID_REQUEST", 422),
        ({"table": "other"}, "items_f0", "WRONG_FRAGMENT", 409),
        ({}, "items_f1", "WRONG_FRAGMENT", 409),
        ({}, "missing", "FRAGMENT_NOT_FOUND", 404),
        ({}, "BAD", "INVALID_REQUEST", 422),
        ({"sql": "INSERT INTO items VALUES (10000,'x')"}, "items_f0", "WRONG_FRAGMENT", 409),
        ({"sql": "DROP TABLE items"}, "items_f0", "UNSUPPORTED_DISTRIBUTED", 400),
        ({"sql": "SELECT ???"}, "items_f0", "SQL_ERROR", 400),
    ],
)
def test_errores_json_sin_mutacion(node_services, changes, fragment, code, status):
    service = node_services["n1"]
    data = envelope("INSERT INTO items VALUES (1,'x')")
    data.update(changes)
    with TestClient(create_node_app(service)) as client:
        result = client.post(f"{PREFIX}/fragments/{fragment}/execute", json=data)
        assert result.status_code == status
        body = ErrorResponse.model_validate(result.json())
        assert body.error.code == code and body.error.execution_state == "not_started"
        assert body.protocol_version == 1 and body.catalog_version == 1 and body.node_id == "n1"
        assert service._database.table("items").size() == 0


@pytest.mark.parametrize(
    "body,content_type",
    [
        ("{", "application/json"),
        ("[]", "application/json"),
        ('{"protocol_version":1,"protocol_version":2}', "application/json"),
        ("{}", "text/plain"),
    ],
)
def test_cuerpo_invalido(node_services, body, content_type):
    with TestClient(create_node_app(node_services["n1"])) as client:
        response = client.post(
            f"{PREFIX}/catalog", content=body, headers={"Content-Type": content_type}
        )
        assert response.status_code == 422
        assert ErrorResponse.model_validate(response.json()).error.code == "INVALID_REQUEST"


def test_error_semantico_con_ubicacion_y_uuid(node_services):
    request = envelope("\nINSERT INTO items VALUES (TRUE,'x')")
    with TestClient(create_node_app(node_services["n1"])) as client:
        response = client.post(f"{PREFIX}/fragments/items_f0/execute", json=request)
        body = response.json()
        assert body["request_id"] == request["request_id"]
        assert body["error"]["kind"] == "semantic"
        assert body["error"]["line"] == 2
