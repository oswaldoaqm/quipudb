"""Solo SELECT * escalar sin operaciones globales en scan."""

import pytest

from engine.distributed.admission import validate_sql
from engine.distributed.node_errors import NodeError


@pytest.mark.parametrize(
    "suffix", ["", "WHERE id=1", "WHERE id<10000", "WHERE nombre='x'", "WHERE id>=-2147483648"]
)
def test_scan_admitido(node_catalog, suffix):
    table = node_catalog.table("items")
    validate_sql(table, table.fragments[0], f"SELECT * FROM items {suffix}", scan=True)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT id FROM items",
        "SELECT * FROM items LIMIT 1",
        "SELECT * FROM items ORDER BY id",
        "SELECT COUNT(*) FROM items",
        "SELECT * FROM items GROUP BY id",
        "EXPLAIN SELECT * FROM items",
        "SELECT * FROM items WHERE id BETWEEN 1 AND 3",
        "SELECT * FROM items JOIN items ON items.id=items.id",
        "SELECT * FROM items WHERE MATCH(nombre, 'x')",
        "INSERT INTO items VALUES (1, 'x')",
    ],
)
def test_formas_no_admitidas(node_catalog, sql):
    table = node_catalog.table("items")
    with pytest.raises(NodeError) as caught:
        validate_sql(table, table.fragments[0], sql, scan=True)
    assert caught.value.code == "UNSUPPORTED_DISTRIBUTED"


def test_execute_no_es_scan(node_catalog):
    table = node_catalog.table("items")
    with pytest.raises(NodeError) as caught:
        validate_sql(table, table.fragments[0], "SELECT * FROM items")
    assert caught.value.code == "UNSUPPORTED_DISTRIBUTED"
