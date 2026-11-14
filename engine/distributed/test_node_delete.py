"""Borrado sobre el fragmento correcto y estabilidad del resto de nodos."""

import pytest

from engine.distributed.node_errors import NodeError
from engine.distributed.node_test_support import envelope, insert, scan


def test_borra_y_repite_sin_afectar_otro_nodo(node_services):
    first, second = node_services["n1"], node_services["n2"]
    insert(first, 9999)
    insert(second, 10000, fragment="items_f1")
    for expected in (1, 0):
        result = first.execute("items_f0", envelope("DELETE FROM items WHERE id=9999"))
        assert result.result.affected_rows == expected
    assert scan(first).rows == []
    assert scan(second, fragment="items_f1").rows == [[10000, "fila"]]


@pytest.mark.parametrize(
    "where,code",
    [
        ("id=10000", "WRONG_FRAGMENT"),
        ("id>=1", "UNSUPPORTED_DISTRIBUTED"),
        ("nombre='fila'", "UNSUPPORTED_DISTRIBUTED"),
    ],
)
def test_rechazo_conserva_filas(node_services, where, code):
    service = node_services["n1"]
    insert(service, 1)
    with pytest.raises(NodeError) as caught:
        service.execute("items_f0", envelope(f"DELETE FROM items WHERE {where}"))
    assert caught.value.code == code
    assert scan(service).rows == [[1, "fila"]]
