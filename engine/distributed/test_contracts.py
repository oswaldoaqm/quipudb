"""Tipos estrictos, esquemas e invariantes locales de fragmentos/tablas."""

from copy import deepcopy

import pytest
from pydantic import ValidationError

from engine.distributed.catalog import DistributedCatalog
from engine.distributed.contracts import Column, Fragment, Node


@pytest.mark.parametrize("value", [True, False, 1.0, "1", -(2**31) - 1, 2**31])
def test_limites_son_int32_estrictos(value):
    with pytest.raises(ValidationError):
        Fragment(fragment_id="f0", node_id="n1", lower=value, upper=None)


@pytest.mark.parametrize("lower,upper", [(1, 1), (2, 1), (None, -(2**31))])
def test_rechaza_intervalos_vacios(lower, upper):
    with pytest.raises(ValidationError, match="vacío"):
        Fragment(fragment_id="f0", node_id="n1", lower=lower, upper=upper)


@pytest.mark.parametrize(
    "url",
    [
        "ftp://n1",
        "http://n1/",
        "http://n1/path",
        "http://n1?x=1",
        "http://n1#x",
        "http://u:p@n1",
        "http://",
        "http://n1:99999",
        "http://n1:0",
        "http://n 1",
        "http://n1?",
        "http://n1\\x",
        "http://n1:",
    ],
)
def test_url_sin_ruta_credenciales_ni_puerto_invalido(url):
    with pytest.raises(ValidationError):
        Node(node_id="n1", base_url=url)


@pytest.mark.parametrize("url", ["http://n1:8000", "https://example.org", "http://[::1]:8000"])
def test_url_valida(url):
    assert Node(node_id="n1", base_url=url).base_url == url


@pytest.mark.parametrize("name", ["N1", "1n", "a-b", "", "n" * 65, "n\n"])
def test_id_de_nodo_invalido(name):
    with pytest.raises(ValidationError):
        Node(node_id=name, base_url="http://n1")


@pytest.mark.parametrize(
    "type_,size",
    [
        ("VARCHAR", None),
        ("VARCHAR", 0),
        ("VECTOR", -1),
        ("INT", 4),
        ("VECTOR", True),
        ("VECTOR", 1.0),
        ("TEXT", None),
    ],
)
def test_tipos_y_tamanos_invalidos(type_, size):
    with pytest.raises(ValidationError):
        Column(name="c", type=type_, size=size)


@pytest.mark.parametrize(
    "change",
    [
        lambda t: t.update(primary_key="inexistente"),
        lambda t: t.update(columns=[]),
        lambda t: t["columns"].append(deepcopy(t["columns"][0])),
        lambda t: t["columns"][0].update(name="bad-name"),
        lambda t: t["columns"][0].update(type="DOUBLE"),
        lambda t: t["columns"][1].update(size=100000),
        lambda t: t.update(storage="sequential"),
        lambda t: t["indexes"][0].update(column="missing"),
        lambda t: t["indexes"][0].update(supports_range=False),
        lambda t: t["indexes"][1].update(supports_range=1),
        lambda t: t["indexes"].append(deepcopy(t["indexes"][0])),
        lambda t: t["indexes"][1].update(structure="rtree"),
        lambda t: t["indexes"][1].update(structure="ivf"),
        lambda t: t["indexes"][1].update(structure="inverted_index"),
        lambda t: t.update(extra=True),
    ],
)
def test_rechaza_esquemas_e_indices_inconsistentes(catalog_data, change):
    change(catalog_data["tables"][0])
    with pytest.raises(ValidationError):
        DistributedCatalog.model_validate(catalog_data)


def test_modelos_inmutables_y_esquema_reutilizable(catalog_data):
    catalog = DistributedCatalog.model_validate(catalog_data)
    table = catalog.table("items")
    assert table.bound_schema.key_column == 0
    assert table.bound_schema.record_size == 84
    assert isinstance(table.fragments, tuple)
    with pytest.raises(ValidationError):
        table.fragments[0].upper = 7
    assert catalog.model_dump(mode="json") == catalog_data


@pytest.mark.parametrize("storage", ["heap", "sequential", "bplus_clustered"])
def test_single_conserva_organizaciones_existentes(catalog_data, storage):
    table = catalog_data["tables"][0]
    table.update(mode="single", storage=storage, indexes=[])
    table["columns"][0].update(type="VARCHAR", size=20)
    table["fragments"] = [{"fragment_id": "f0", "node_id": "n1", "lower": None, "upper": None}]
    assert DistributedCatalog.model_validate(catalog_data).table("items").mode == "single"


def test_indices_especializados_respetan_tipos(catalog_data):
    table = catalog_data["tables"][0]
    table.update(mode="single")
    table["fragments"] = [{"fragment_id": "f0", "node_id": "n1", "lower": None, "upper": None}]
    table["columns"].extend(
        [
            {"name": "p", "type": "POINT", "size": None},
            {"name": "v", "type": "VECTOR", "size": 4},
        ]
    )
    for name, col, kind in [
        ("texto", "nombre", "inverted_index"),
        ("p", "p", "rtree"),
        ("v", "v", "hnsw"),
        ("v2", "v", "ivf"),
    ]:
        table["indexes"].append(
            {"name": name, "column": col, "structure": kind, "supports_range": False}
        )
    assert len(DistributedCatalog.model_validate(catalog_data).table("items").indexes) == 6
