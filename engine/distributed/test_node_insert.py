"""Inserciones reales, duplicados, límites y serialización concurrente."""

from concurrent.futures import ThreadPoolExecutor

import pytest

from engine.distributed.node_errors import NodeError
from engine.distributed.node_test_support import envelope, insert, scan


@pytest.mark.parametrize(
    "key,node,fragment",
    [
        (-2147483648, "n1", "items_f0"),
        (9999, "n1", "items_f0"),
        (10000, "n2", "items_f1"),
        (2147483647, "n2", "items_f1"),
    ],
)
def test_inserta_solo_propietario(node_services, key, node, fragment):
    result = insert(node_services[node], key, fragment=fragment)
    assert result.result.affected_rows == 1
    assert result.node_id == node and result.fragment_id == fragment
    assert scan(node_services[node], fragment=fragment).rows == [[key, "fila"]]
    other, other_fragment = ("n2", "items_f1") if node == "n1" else ("n1", "items_f0")
    assert scan(node_services[other], fragment=other_fragment).rows == []


def test_rechazo_no_modifica_ningun_fragmento(node_services):
    with pytest.raises(NodeError) as caught:
        insert(node_services["n1"], 10000)
    assert caught.value.code == "WRONG_FRAGMENT"
    assert caught.value.detail.execution_state == "not_started"
    assert scan(node_services["n1"]).rows == []
    assert scan(node_services["n2"], fragment="items_f1").rows == []


def test_request_id_no_es_deduplicacion(node_services):
    service = node_services["n1"]
    request = envelope("INSERT INTO items VALUES (1, 'x')")
    service.execute("items_f0", request)
    with pytest.raises(NodeError) as caught:
        service.execute("items_f0", request)
    assert caught.value.code == "DUPLICATE_KEY"
    assert caught.value.detail.execution_state == "unknown"
    assert scan(service).rows == [[1, "x"]]


def test_concurrencia_conserva_unicidad(node_services):
    service = node_services["n1"]

    def attempt(_):
        try:
            insert(service, 42)
            return "ok"
        except NodeError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(attempt, range(12)))
    assert results.count("ok") == 1
    assert results.count("DUPLICATE_KEY") == 11
    assert scan(service).rows == [[42, "fila"]]
