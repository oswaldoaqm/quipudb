"""Configuración de ejemplo real compartida por las pruebas de #276."""

import json
from pathlib import Path

import pytest


@pytest.fixture
def catalog_data():
    path = Path(__file__).resolve().parents[2] / "docs/ejemplos/catalogo-distribuido-v1.json"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def node_catalog(catalog_data):
    from engine.distributed.catalog import DistributedCatalog

    return DistributedCatalog.model_validate(catalog_data)


@pytest.fixture
def node_services(tmp_path, node_catalog):
    pytest.importorskip("quipudb_native", reason="operaciones requieren motor real")
    from engine.distributed.fragments import FragmentService
    from engine.distributed.storage import prepare_range_node

    services = {}
    try:
        for node in ("n1", "n2"):
            directory = tmp_path / node
            prepare_range_node(node_catalog, node, directory)
            service = FragmentService(node_catalog, node, directory)
            services[node] = service
            service.start()
            assert service.health().status == "ready"
        yield services
    finally:
        for service in services.values():
            service.close()
