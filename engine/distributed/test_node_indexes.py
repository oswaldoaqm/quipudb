"""Índices nativos coherentes tras inserción, duplicado y eliminación."""

import pytest

from engine.distributed.node_errors import NodeError
from engine.distributed.node_test_support import envelope, insert


def test_bplus_hash_y_entradas_repetidas(node_services):
    service = node_services["n1"]
    insert(service, 1, "igual")
    insert(service, 2, "igual")
    db = service._database
    assert len(db.index("items", "por_nombre").search("igual")) == 2
    assert len(db.index("items", "por_id").search(1)) == 1
    with pytest.raises(NodeError):
        insert(service, 1, "no_debe_indexarse")
    assert db.index("items", "por_nombre").search("no_debe_indexarse") == []
    service.execute("items_f0", envelope("DELETE FROM items WHERE id=1"))
    assert db.index("items", "por_id").search(1) == []
    rids = db.index("items", "por_nombre").search("igual")
    assert len(rids) == 1 and db.table("items").read(rids[0]) == [2, "igual"]
