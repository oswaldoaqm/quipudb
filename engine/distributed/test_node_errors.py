"""Códigos HTTP y contexto de errores del contrato v1."""

import pytest

from engine.distributed.node_errors import NodeError, sql_error
from engine.distributed.protocol import ErrorResponse
from engine.parser import parse_sql_script
from engine.parser.errors import SQLError


@pytest.mark.parametrize(
    "code,status",
    [
        ("INVALID_REQUEST", 422),
        ("PROTOCOL_VERSION_MISMATCH", 409),
        ("CATALOG_VERSION_MISMATCH", 409),
        ("SCHEMA_MISMATCH", 409),
        ("FRAGMENT_NOT_FOUND", 404),
        ("WRONG_FRAGMENT", 409),
        ("UNSUPPORTED_DISTRIBUTED", 400),
        ("SQL_ERROR", 400),
        ("DUPLICATE_KEY", 409),
        ("NODE_NOT_READY", 503),
        ("EXECUTION_ERROR", 500),
    ],
)
def test_codigos_y_sobre(node_catalog, code, status):
    error = NodeError(code, "mensaje")
    assert error.status == status
    result = error.response(node_catalog, "n1").model_dump(mode="json")
    assert ErrorResponse.model_validate(result).error.execution_state == "not_started"
    assert result["request_id"] is None and result["fragment_id"] is None
    assert result["error"]["message"] == "mensaje"


def test_contexto_sql_preserva_posiciones():
    with pytest.raises(SQLError) as caught:
        parse_sql_script("\nSELECT ???")
    converted = sql_error(caught.value)
    assert converted.detail.line == 2
    assert converted.detail.column == caught.value.column
    assert converted.detail.message == caught.value.message
