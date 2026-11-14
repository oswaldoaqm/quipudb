"""Borrado por PK validada, incluyendo calificación de columnas."""

import pytest

from engine.distributed.admission import validate_sql
from engine.distributed.node_errors import NodeError


@pytest.mark.parametrize(
    "where,code",
    [
        ("id=10000", "WRONG_FRAGMENT"),
        ("id<5", "UNSUPPORTED_DISTRIBUTED"),
        ("nombre='x'", "UNSUPPORTED_DISTRIBUTED"),
        ("id=TRUE", "SQL_ERROR"),
        ("otra.id=1", "SQL_ERROR"),
    ],
)
def test_borrado_rechazado(node_catalog, where, code):
    table = node_catalog.table("items")
    with pytest.raises(NodeError) as caught:
        validate_sql(table, table.fragments[0], f"DELETE FROM items WHERE {where}")
    assert caught.value.code == code


def test_borrado_admitido(node_catalog):
    table = node_catalog.table("items")
    validate_sql(table, table.fragments[0], "DELETE FROM items WHERE items.id=9999")
