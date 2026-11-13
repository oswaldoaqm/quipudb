"""Demostración offline #276: procesos reales separados, sin HTTP ni coordinador.

Ejecutar desde la raíz: python -m scripts.demo_fragmentacion --root /tmp/demo276
Usar --verify-only para comprobar persistencia sin insertar de nuevo.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from engine.distributed.catalog import load_catalog
from engine.distributed.contracts import INT32_MAX, INT32_MIN
from engine.distributed.partitioning import owner_for_key
from engine.distributed.storage import prepare_range_node, verify_range_node
from engine.executor import QueryProcessor
from engine.executor.native import load_native

DEFAULT_CATALOG = Path(__file__).resolve().parents[1] / "docs/ejemplos/catalogo-distribuido-v1.json"


def _example(catalog):
    table = catalog.table("items")
    if (
        len(catalog.tables) != 1
        or table.mode != "range"
        or [(c.name, c.type, c.size) for c in table.columns]
        != [("id", "INT", None), ("nombre", "VARCHAR", 80)]
    ):
        raise ValueError(
            "la demo requiere el esquema items(id INT, nombre VARCHAR(80)) del ejemplo"
        )
    keys = {INT32_MIN, INT32_MAX}
    for fragment in table.ordered_fragments:
        if fragment.lower is not None:
            keys.update(
                k
                for k in (fragment.lower - 1, fragment.lower, fragment.lower + 1)
                if INT32_MIN <= k <= INT32_MAX
            )
    return table, sorted(keys)


def _worker(config: Path, root: Path, node_id: str, phase: str) -> dict:
    catalog = load_catalog(config)
    table, keys = _example(catalog)
    directory = root / node_id
    if phase == "seed":
        prepare_range_node(catalog, node_id, directory)
    else:
        verify_range_node(catalog, node_id, directory)
    native = load_native()
    database = native.Database(directory / "catalogo.txt")
    processor = QueryProcessor(database)
    expected = [k for k in keys if owner_for_key(table, k).node_id == node_id]
    try:
        if phase == "seed":
            for key in expected:
                processor.execute(f"INSERT INTO items VALUES ({key}, 'fila {key}')")
        rows = processor.execute("SELECT * FROM items ORDER BY id").rows
        if rows != tuple((k, f"fila {k}") for k in expected):
            raise ValueError(f"filas inesperadas en {node_id}: {rows}")
        for key in keys:
            # Comprueba búsquedas locales e índices, incluidas claves ajenas.
            result = processor.execute(f"SELECT id FROM items WHERE nombre = 'fila {key}'")
            if result.rows != (((key,),) if key in expected else ()):
                raise ValueError(f"búsqueda incorrecta de {key} en {node_id}")
    finally:
        database.flush()
        for name in database.open_tables():
            database.close(name)
    counts = verify_range_node(catalog, node_id, directory)
    return {
        "phase": phase,
        "pid": os.getpid(),
        "node_id": node_id,
        "catalog_path": str((directory / "catalogo.txt").resolve()),
        "keys": expected,
        "counts": counts,
        "files": sorted(p.name for p in directory.iterdir() if p.is_file()),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--worker", help=argparse.SUPPRESS)
    parser.add_argument("--phase", choices=("seed", "verify"), help=argparse.SUPPRESS)
    args = parser.parse_args()
    catalog = load_catalog(args.catalog)
    table, _ = _example(catalog)
    if args.worker:
        if not args.phase:
            parser.error("--worker requiere --phase")
        print(json.dumps(_worker(args.catalog, args.root, args.worker, args.phase)))
        return
    # Fallar antes de preparar el primer nodo si una ruta ya está ocupada.
    node_ids = [f.node_id for f in table.ordered_fragments]
    if not args.verify_only and any((args.root / n).exists() for n in node_ids):
        parser.error("directorios de nodos existentes; usa --verify-only o una raíz nueva")
    phases = ("verify",) if args.verify_only else ("seed", "verify")
    for phase in phases:
        # Un proceso por nodo, ejecutados concurrentemente con archivos separados.
        workers = [
            subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "scripts.demo_fragmentacion",
                    "--catalog",
                    str(args.catalog.resolve()),
                    "--root",
                    str(args.root.resolve()),
                    "--worker",
                    node,
                    "--phase",
                    phase,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for node in node_ids
        ]
        failed = False
        for worker in workers:
            stdout, stderr = worker.communicate()
            if worker.returncode:
                failed = True
                print(stderr, file=sys.stderr)
            else:
                print(stdout.strip())
        if failed:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
