"""Los ejemplos normativos de #275 se consumen sin otro formato JSON."""

import json
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from engine.distributed.catalog import DistributedCatalog
from engine.distributed.protocol import (
    BaseRequest,
    CatalogResponse,
    ErrorResponse,
    ExecutionResponse,
    FragmentRequest,
    HealthResponse,
)


def test_todos_los_ejemplos_del_contrato():
    path = Path(__file__).resolve().parents[2] / "docs/contrato-distribuido-v1.md"
    examples = re.findall(r"```json\n(.*?)\n```", path.read_text(), re.DOTALL)
    models = [
        DistributedCatalog,
        HealthResponse,
        BaseRequest,
        CatalogResponse,
        FragmentRequest,
        ExecutionResponse,
        FragmentRequest,
        ErrorResponse,
    ]
    assert len(examples) == len(models)
    for raw, model in zip(examples, models, strict=True):
        data = json.loads(raw)
        assert model.model_validate(data).model_dump(mode="json") == data


@pytest.mark.parametrize(
    "change",
    [
        {"request_id": "DE783F4E-A813-4FA1-86EC-B7E3ABC40291"},
        {"request_id": "de783f4ea8134fa186ecb7e3abc40291"},
        {"request_id": "bad"},
        {"request_id": None},
        {"extra": 1},
        {"protocol_version": True},
        {"protocol_version": 2},
        {"catalog_version": False},
    ],
)
def test_sobre_rechaza_tipos_versiones_y_uuid_incorrectos(change):
    data = {
        "protocol_version": 1,
        "catalog_version": 1,
        "request_id": "de783f4e-a813-4fa1-86ec-b7e3abc40291",
    }
    data.update(change)
    with pytest.raises(ValidationError):
        BaseRequest.model_validate(data)


def test_respuesta_admite_diagnostico_aditivo():
    response = HealthResponse.model_validate(
        {
            "protocol_version": 1,
            "catalog_version": 1,
            "node_id": "n1",
            "status": "ready",
            "fragment_ids": [],
            "reason": None,
            "diagnostic": "ok",
        }
    )
    assert "diagnostic" not in response.model_dump()
