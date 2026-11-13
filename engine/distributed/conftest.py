"""Configuración de ejemplo real compartida por las pruebas de #276."""

import json
from pathlib import Path

import pytest


@pytest.fixture
def catalog_data():
    path = Path(__file__).resolve().parents[2] / "docs/ejemplos/catalogo-distribuido-v1.json"
    return json.loads(path.read_text(encoding="utf-8"))
