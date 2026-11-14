"""Fronteras y selección comparadas con un oráculo de claves concretas."""

import operator

import pytest

from engine.distributed.catalog import DistributedCatalog
from engine.distributed.contracts import INT32_MAX, INT32_MIN
from engine.distributed.partitioning import fragments_for_comparison, owner_for_key


@pytest.mark.parametrize(
    "key,node,fragment",
    [
        (INT32_MIN, "n1", "items_f0"),
        (9999, "n1", "items_f0"),
        (10000, "n2", "items_f1"),
        (10001, "n2", "items_f1"),
        (INT32_MAX, "n2", "items_f1"),
    ],
)
def test_pertenencia_y_determinismo(catalog_data, key, node, fragment):
    table = DistributedCatalog.model_validate(catalog_data).table("items")
    for _ in range(5):
        actual = owner_for_key(table, key)
        assert (actual.node_id, actual.fragment_id) == (node, fragment)


@pytest.mark.parametrize("value", [True, False, None, "42", 42.0, INT32_MIN - 1, INT32_MAX + 1])
def test_claves_invalidas(catalog_data, value):
    table = DistributedCatalog.model_validate(catalog_data).table("items")
    with pytest.raises(ValueError):
        owner_for_key(table, value)
    with pytest.raises(ValueError):
        fragments_for_comparison(table, "=", value)


@pytest.mark.parametrize(
    "symbol,comparison",
    [
        ("=", operator.eq),
        ("<", operator.lt),
        ("<=", operator.le),
        (">", operator.gt),
        (">=", operator.ge),
    ],
)
@pytest.mark.parametrize("boundary", [INT32_MIN + 1, -1, 0, 17, 10000, INT32_MAX])
def test_seleccion_exacta_en_extremos_y_fronteras(catalog_data, boundary, symbol, comparison):
    fragments = catalog_data["tables"][0]["fragments"]
    fragments[0]["upper"] = boundary
    fragments[1]["lower"] = boundary
    # El JSON no necesita ordenar los fragmentos físicamente.
    fragments.reverse()
    table = DistributedCatalog.model_validate(catalog_data).table("items")
    values = {INT32_MIN, INT32_MAX, boundary, boundary - 1}
    if boundary < INT32_MAX:
        values.add(boundary + 1)
    for value in values:
        # Para una comparación monótona basta comprobar extremos efectivos;
        # igualdad se comprueba con la clave misma, sin usar el algoritmo probado.
        expected = []
        for f in table.ordered_fragments:
            lo = INT32_MIN if f.lower is None else f.lower
            hi = INT32_MAX if f.upper is None else f.upper - 1
            witnesses = {lo, hi, value} if lo <= value <= hi else {lo, hi}
            if any(comparison(k, value) for k in witnesses):
                expected.append(f.fragment_id)
        actual = fragments_for_comparison(table, symbol, value)
        assert [f.fragment_id for f in actual] == expected


def test_multiples_rangos_y_cambio_de_configuracion(catalog_data):
    catalog_data["nodes"].append({"node_id": "n3", "base_url": "http://n3:8000"})
    table_data = catalog_data["tables"][0]
    table_data["fragments"] = [
        {"fragment_id": "f2", "node_id": "n3", "lower": 20, "upper": None},
        {"fragment_id": "f0", "node_id": "n1", "lower": None, "upper": -20},
        {"fragment_id": "f1", "node_id": "n2", "lower": -20, "upper": 20},
    ]
    table = DistributedCatalog.model_validate(catalog_data).table("items")
    assert owner_for_key(table, 0).node_id == "n2"
    assert [f.fragment_id for f in fragments_for_comparison(table, "<", 20)] == ["f0", "f1"]
    assert fragments_for_comparison(table, "<", INT32_MIN) == ()
    assert fragments_for_comparison(table, ">", INT32_MAX) == ()
    with pytest.raises(ValueError):
        fragments_for_comparison(table, "!=", 0)


def test_single_no_pretende_ser_particion_int(catalog_data):
    data = catalog_data["tables"][0]
    data.update(
        mode="single",
        fragments=[
            {
                "fragment_id": "f0",
                "node_id": "n1",
                "lower": None,
                "upper": None,
            }
        ],
    )
    table = DistributedCatalog.model_validate(catalog_data).table("items")
    with pytest.raises(ValueError, match="range"):
        owner_for_key(table, 1)
