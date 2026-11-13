"""Admisión de INSERT sin abrir el núcleo."""

import pytest

from engine.distributed.admission import validate_sql
from engine.distributed.node_errors import NodeError


@pytest.mark.parametrize("key,fragment", [(-2147483648, 0), (9999, 0), (10000, 1), (2147483647, 1)])
def test_claves_validas(node_catalog, key, fragment):
    table = node_catalog.table("items")
    validate_sql(table, table.fragments[fragment], f"INSERT INTO items VALUES ({key}, 'x')")


@pytest.mark.parametrize(
    "sql,code",
    [
        ("INSERT INTO items VALUES (10000, 'x')", "WRONG_FRAGMENT"),
        ("INSERT INTO otra VALUES (1, 'x')", "WRONG_FRAGMENT"),
        ("INSERT INTO items VALUES (TRUE, 'x')", "SQL_ERROR"),
        ("INSERT INTO items VALUES (2147483648, 'x')", "SQL_ERROR"),
        ("INSERT INTO items VALUES (1)", "SQL_ERROR"),
        (
            "INSERT INTO items VALUES (1, 'x'); DELETE FROM items WHERE id=1",
            "UNSUPPORTED_DISTRIBUTED",
        ),
        ("BEGIN TRANSACTION", "UNSUPPORTED_DISTRIBUTED"),
        ("DROP TABLE items", "UNSUPPORTED_DISTRIBUTED"),
    ],
)
def test_rechazos_sin_motor(node_catalog, sql, code):
    table = node_catalog.table("items")
    with pytest.raises(NodeError) as caught:
        validate_sql(table, table.fragments[0], sql)
    assert caught.value.code == code
    assert caught.value.detail.execution_state == "not_started"
