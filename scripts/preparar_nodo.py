"""Prepara el directorio de datos de un nodo range, solo si todavía no existe.

Crea las tablas y los índices que el catálogo distribuido asigna al nodo, con
``prepare_range_node`` del #276. Si el directorio ya tiene ``catalogo.txt`` no
toca nada: así se puede correr antes de cada arranque, como hace
``docker-compose.distribuido.yml``.

    python -m scripts.preparar_nodo --catalog catalogo.json --catalog-version 1 \\
        --node-id n1 --data-dir datos/n1
"""

from __future__ import annotations

import argparse
from pathlib import Path

from engine.distributed.catalog import load_catalog
from engine.distributed.storage import prepare_range_node


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--catalog-version", type=int, required=True)
    parser.add_argument("--node-id", required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    args = parser.parse_args()

    if (args.data_dir / "catalogo.txt").is_file():
        print(f"{args.node_id}: {args.data_dir} ya está preparado")
        return
    catalog = load_catalog(args.catalog, expected_catalog_version=args.catalog_version)
    prepare_range_node(catalog, args.node_id, args.data_dir)
    fragments = [f.fragment_id for f in catalog.fragments_for_node(args.node_id)]
    print(f"{args.node_id}: preparado en {args.data_dir} con {', '.join(fragments)}")


if __name__ == "__main__":
    main()
