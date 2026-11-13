"""Motor real: procesos separados, archivos, índices y reapertura persistente."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from engine.distributed.catalog import DistributedCatalog
from engine.distributed.storage import prepare_range_node, verify_range_node
from engine.executor import QueryProcessor

native = pytest.importorskip("quipudb_native", reason="requiere bindings reales compilados")
ROOT = Path(__file__).resolve().parents[2]


def _demo(config, root, *extra):
    env = os.environ.copy()
    # También funciona si pytest importó los bindings desde un sys.path custom.
    env["PYTHONPATH"] = os.pathsep.join(
        [
            str(Path(native.__file__).parent),
            str(ROOT),
            env.get("PYTHONPATH", ""),
        ]
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.demo_fragmentacion",
            "--catalog",
            str(config),
            "--root",
            str(root),
            *extra,
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return [json.loads(line) for line in result.stdout.splitlines()]


@pytest.mark.parametrize("boundary", [10000, -5])
def test_dos_procesos_almacenamiento_y_persistencia(tmp_path, catalog_data, boundary):
    fragments = catalog_data["tables"][0]["fragments"]
    fragments[0]["upper"] = boundary
    fragments[1]["lower"] = boundary
    config = tmp_path / "cluster.json"
    config.write_text(json.dumps(catalog_data))
    root = tmp_path / "nodes"
    events = _demo(config, root)
    assert len(events) == 4
    assert len({e["pid"] for e in events}) == 4
    assert [e["phase"] for e in events] == ["seed", "seed", "verify", "verify"]
    assert events[0]["keys"] == [-(2**31), boundary - 1]
    assert events[1]["keys"] == [boundary, boundary + 1, 2**31 - 1]
    for original, reopened in zip(events[:2], events[2:], strict=True):
        assert original["keys"] == reopened["keys"]
        assert original["counts"] == reopened["counts"]
        assert {
            "catalogo.txt",
            "items.heap",
            "items.por_id.bplus_unclustered",
            "items.por_nombre.extendible_hash",
        } == set(original["files"])
    for name in events[0]["files"]:
        first, second = root / "n1" / name, root / "n2" / name
        assert first.resolve() != second.resolve()
        assert not first.samefile(second)
    # Tercera apertura: ningún proceso de carga sigue vivo.
    reopened = _demo(config, root, "--verify-only")
    assert [e["keys"] for e in reopened] == [e["keys"] for e in events[:2]]


def test_no_adopta_directorio_existente(tmp_path, catalog_data):
    catalog = DistributedCatalog.model_validate(catalog_data)
    directory = tmp_path / "node"
    path = prepare_range_node(catalog, "n1", directory)
    before = path.read_bytes()
    with pytest.raises(FileExistsError):
        prepare_range_node(catalog, "n2", directory)
    assert path.read_bytes() == before
    assert verify_range_node(catalog, "n1", directory) == {"items_f0": 0}


def test_detecta_registro_en_fragmento_incorrecto(tmp_path, catalog_data):
    catalog = DistributedCatalog.model_validate(catalog_data)
    directory = tmp_path / "node"
    path = prepare_range_node(catalog, "n1", directory)
    database = native.Database(path)
    # Evasión deliberada del futuro servicio #277 para comprobar la auditoría.
    QueryProcessor(database).execute("INSERT INTO items VALUES (10000, 'incorrecto')")
    database.flush()
    database.close("items")
    del database
    with pytest.raises(ValueError, match="fuera del fragmento"):
        verify_range_node(catalog, "n1", directory)


def test_detecta_metadata_incompatible(tmp_path, catalog_data):
    catalog = DistributedCatalog.model_validate(catalog_data)
    directory = tmp_path / "node"
    path = prepare_range_node(catalog, "n1", directory)
    database = native.Database(path)
    database.drop_index("items", "por_nombre")
    database.flush()
    del database
    with pytest.raises(ValueError, match="incompatibles"):
        verify_range_node(catalog, "n1", directory)


def test_auditoria_no_crea_catalogo_ausente(tmp_path, catalog_data):
    catalog = DistributedCatalog.model_validate(catalog_data)
    with pytest.raises(ValueError, match="existente"):
        verify_range_node(catalog, "n1", tmp_path / "missing")
    assert not (tmp_path / "missing").exists()


def test_preparacion_rechaza_indice_que_requiere_configuracion(tmp_path, catalog_data):
    table = catalog_data["tables"][0]
    table["columns"].append({"name": "v", "type": "VECTOR", "size": 4})
    table["indexes"].append(
        {
            "name": "v",
            "column": "v",
            "structure": "ivf",
            "supports_range": False,
        }
    )
    catalog = DistributedCatalog.model_validate(catalog_data)
    with pytest.raises(ValueError, match="IVF/HNSW"):
        prepare_range_node(catalog, "n1", tmp_path / "node")
    assert not (tmp_path / "node").exists()
