"""Lectura local de filas y metadatos vacíos con core real."""

import pytest

from engine.distributed.node_test_support import insert, scan


@pytest.mark.parametrize(
    "where,keys",
    [
        ("", [1, 2]),
        ("WHERE id=1", [1]),
        ("WHERE id=999", []),
        ("WHERE id<2", [1]),
        ("WHERE id<=2", [1, 2]),
        ("WHERE id>1", [2]),
        ("WHERE id>=2", [2]),
        ("WHERE nombre='b'", [2]),
    ],
)
def test_filtros_locales(node_services, where, keys):
    service = node_services["n1"]
    insert(service, 1, "a")
    insert(service, 2, "b")
    insert(node_services["n2"], 10000, fragment="items_f1")
    result = scan(service, where)
    assert sorted(row[0] for row in result.rows) == keys
    assert result.columns == ["id", "nombre"]
    assert result.column_types == ["INT", "VARCHAR"]
    assert result.affected_rows == 0 and result.plan is not None
