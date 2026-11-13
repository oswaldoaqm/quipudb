"""Versiones, identidad y JSON se rechazan antes de acceder a almacenamiento."""

import pytest

from engine.distributed.node_errors import NodeError
from engine.distributed.node_requests import (
    decode_body,
    request_id_of,
    resolve_fragment,
    validate_request,
)
from engine.distributed.node_test_support import envelope


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("protocol_version", 2, "PROTOCOL_VERSION_MISMATCH"),
        ("catalog_version", 2, "CATALOG_VERSION_MISMATCH"),
        ("protocol_version", True, "INVALID_REQUEST"),
        ("protocol_version", "1", "INVALID_REQUEST"),
        ("catalog_version", 0, "INVALID_REQUEST"),
        ("catalog_version", 1.0, "INVALID_REQUEST"),
        ("request_id", "bad", "INVALID_REQUEST"),
        ("node_id", "n2", "INVALID_REQUEST"),
    ],
)
def test_versiones_y_campos(node_catalog, field, value, code):
    with pytest.raises(NodeError) as caught:
        validate_request(envelope(**{field: value}), node_catalog)
    assert caught.value.code == code
    assert caught.value.detail.execution_state == "not_started"


@pytest.mark.parametrize("raw", [b"{", b"[]", b"null", b"{}\xff", b'{"a":1,"a":2}', b'{"x":NaN}'])
def test_json_estricto(raw):
    with pytest.raises(NodeError) as caught:
        decode_body(raw)
    assert caught.value.status == 422


@pytest.mark.parametrize(
    "fragment,table,code",
    [
        ("missing", "items", "FRAGMENT_NOT_FOUND"),
        ("items_f1", "items", "WRONG_FRAGMENT"),
        ("items_f0", "otra", "WRONG_FRAGMENT"),
        ("BAD", "items", "INVALID_REQUEST"),
    ],
)
def test_identidad_y_propietario(node_catalog, fragment, table, code):
    with pytest.raises(NodeError) as caught:
        resolve_fragment(node_catalog, "n1", fragment, table)
    assert caught.value.code == code


def test_request_id_solo_si_es_valido(node_catalog):
    data = envelope()
    assert request_id_of(data) == data["request_id"]
    assert request_id_of({"request_id": "bad"}) is None
    table, fragment = resolve_fragment(node_catalog, "n1", "items_f0", "items")
    assert table.name == "items" and fragment.node_id == "n1"
