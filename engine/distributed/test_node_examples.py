"""Los archivos usados por los comandos curl son ejecutables contra el nodo."""

import json
from pathlib import Path

from fastapi.testclient import TestClient

from engine.distributed.catalog import load_catalog
from engine.distributed.node_api import create_node_app
from engine.distributed.protocol import FragmentRequest


def test_ejemplos_de_documentacion(node_services):
    directory = Path(__file__).resolve().parents[2] / "docs/ejemplos"
    catalog = load_catalog(directory / "catalogo-nodos-locales-v1.json")
    assert catalog.node("n1").base_url == "http://127.0.0.1:8001"
    assert catalog.node("n2").base_url == "http://127.0.0.1:8002"
    with TestClient(create_node_app(node_services["n1"])) as client:
        for operation, endpoint in [("insert", "execute"), ("scan", "scan"), ("delete", "execute")]:
            data = json.loads((directory / f"nodo-{operation}-v1.json").read_text())
            assert FragmentRequest.model_validate(data).table == "items"
            response = client.post(f"/internal/v1/fragments/items_f0/{endpoint}", json=data)
            assert response.status_code == 200, response.text
            result = response.json()["result"]
            if operation == "scan":
                assert result["rows"] == [[42, "Quipu"]]
            else:
                assert result["affected_rows"] == 1
