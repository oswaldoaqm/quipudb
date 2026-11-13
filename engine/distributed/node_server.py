"""Arranque explícito de un nodo, sin crear tablas ni modificar topología."""

import argparse
import json
from pathlib import Path

import uvicorn

from engine.distributed.catalog import load_catalog
from engine.distributed.fragments import FragmentService
from engine.distributed.node_api import create_node_app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--catalog-version", type=int, required=True)
    parser.add_argument("--node-id", required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--media-root", type=Path)
    parser.add_argument("--media-vocabularies", type=Path, help="JSON tabla/columna -> ruta local")
    args = parser.parse_args()
    vocabularies = (
        json.loads(args.media_vocabularies.read_text(encoding="utf-8"))
        if args.media_vocabularies
        else {}
    )
    if not isinstance(vocabularies, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in vocabularies.items()
    ):
        parser.error("media-vocabularies requiere un objeto de cadenas")
    catalog = load_catalog(args.catalog, expected_catalog_version=args.catalog_version)
    service = FragmentService(
        catalog,
        args.node_id,
        args.data_dir,
        media_root=args.media_root,
        media_vocabularies=vocabularies,
    )
    uvicorn.run(create_node_app(service), host=args.host, port=args.port, workers=1)


if __name__ == "__main__":
    main()
