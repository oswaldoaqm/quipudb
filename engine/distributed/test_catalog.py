"""Catálogo persistente, referencias globales y cobertura del dominio."""

import json
from copy import deepcopy

import pytest
from pydantic import ValidationError

from engine.distributed.catalog import CatalogError, DistributedCatalog, load_catalog


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d.update(protocol_version=2),
        lambda d: d.update(protocol_version=True),
        lambda d: d.update(catalog_version=0),
        lambda d: d.update(catalog_version="1"),
        lambda d: d.update(nodes=d["nodes"][:1]),
        lambda d: d["nodes"].append(deepcopy(d["nodes"][0])),
        lambda d: d["nodes"][1].update(base_url=d["nodes"][0]["base_url"]),
        lambda d: d["tables"].append(deepcopy(d["tables"][0])),
        lambda d: d["tables"][0]["fragments"][1].update(node_id="missing"),
        lambda d: d["tables"][0]["fragments"][1].update(node_id="n1"),
        lambda d: d["tables"][0]["fragments"][1].update(fragment_id="items_f0"),
        lambda d: d["tables"][0]["fragments"][1].update(lower=9999),
        lambda d: d["tables"][0]["fragments"][1].update(lower=10001),
        lambda d: d["tables"][0]["fragments"][0].update(lower=0),
        lambda d: d["tables"][0]["fragments"][1].update(upper=2147483647),
        lambda d: d["tables"][0]["fragments"][0].update(upper=None),
        lambda d: d["tables"][0]["fragments"][1].update(lower=None),
        lambda d: d["tables"][0].update(fragments=[]),
        lambda d: d["tables"][0].update(fragments=d["tables"][0]["fragments"][:1]),
        lambda d: d["tables"][0].update(mode="single"),
        lambda d: d.update(nodes=None),
        lambda d: d.update(tables="items"),
    ],
)
def test_rechaza_configuracion_inconsistente(catalog_data, change):
    change(catalog_data)
    with pytest.raises(ValidationError):
        DistributedCatalog.model_validate(catalog_data)


def test_fragmentos_unicos_entre_tablas(catalog_data):
    other = deepcopy(catalog_data["tables"][0])
    other["name"] = "other"
    catalog_data["tables"].append(other)
    with pytest.raises(ValidationError, match="global"):
        DistributedCatalog.model_validate(catalog_data)


def test_carga_persistencia_y_consultas(tmp_path, catalog_data):
    path = tmp_path / "cluster.json"
    original = json.dumps(catalog_data).encode()
    path.write_bytes(original)
    first = load_catalog(path, expected_catalog_version=1)
    second = load_catalog(path)
    assert first == second
    assert path.read_bytes() == original
    assert first.node("n2").base_url == "http://n2:8000"
    assert [f.fragment_id for f in first.fragments_for_node("n1")] == ["items_f0"]
    for lookup in (first.node, first.table, first.fragments_for_node):
        with pytest.raises(KeyError):
            lookup("missing")
    with pytest.raises(CatalogError, match="catalog_version incompatible"):
        load_catalog(path, expected_catalog_version=2)
    for version in (True, 0, "1"):
        with pytest.raises(CatalogError):
            load_catalog(path, expected_catalog_version=version)


@pytest.mark.parametrize("field", ["protocol_version", "catalog_version", "nodes", "tables"])
def test_campos_requeridos(tmp_path, catalog_data, field):
    del catalog_data[field]
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(catalog_data))
    with pytest.raises(CatalogError):
        load_catalog(path)


@pytest.mark.parametrize(
    "content",
    [
        "{",
        "[]",
        "null",
        '{"a":1,"a":2}',
        '{"a":NaN}',
        '{"a":Infinity}',
    ],
)
def test_json_invalido(tmp_path, content):
    path = tmp_path / "bad.json"
    path.write_text(content)
    with pytest.raises(CatalogError):
        load_catalog(path)


def test_archivo_ausente_o_no_utf8(tmp_path):
    path = tmp_path / "bad.json"
    with pytest.raises(CatalogError):
        load_catalog(path)
    path.write_bytes(b"\xff")
    with pytest.raises(CatalogError):
        load_catalog(path)


def test_version_positiva_no_es_numero_de_protocolo(catalog_data):
    catalog_data["catalog_version"] = 42
    assert DistributedCatalog.model_validate(catalog_data).catalog_version == 42
