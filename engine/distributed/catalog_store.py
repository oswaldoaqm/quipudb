"""Copia persistente del catálogo distribuido que cambió por DDL (#296).

El catálogo de arranque es un JSON de solo lectura (en Docker se monta así).
Cuando un ``CREATE TABLE`` o ``DROP TABLE`` lo cambia, el nodo y el
coordinador guardan la versión nueva en su directorio de datos. Al volver a
arrancar se usa la guardada si es más nueva que la de arranque; si no, la de
arranque. Así una tabla creada en la demo sigue existiendo después de
reiniciar los contenedores.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from engine.distributed.catalog import CatalogError, DistributedCatalog, load_catalog

FILENAME = "catalogo-distribuido.json"


def save_catalog(directory: str | Path, catalog: DistributedCatalog) -> Path:
    """Escribe ``catalog`` de forma atómica: o queda el archivo viejo o el nuevo."""

    directory = Path(directory)
    path = directory / FILENAME
    temporary = directory / f".{FILENAME}.tmp"
    text = json.dumps(catalog.model_dump(mode="json"), ensure_ascii=False, indent=2)
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(text + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    return path


def newest_catalog(catalog: DistributedCatalog, directory: str | Path) -> DistributedCatalog:
    """El guardado en ``directory`` si es más nuevo que ``catalog``; si no, ``catalog``.

    Rechaza un guardado con otros nodos: significaría que el directorio es de
    otro despliegue.
    """

    path = Path(directory) / FILENAME
    if not path.is_file():
        return catalog
    stored = load_catalog(path)
    if stored.catalog_version <= catalog.catalog_version:
        return catalog
    if stored.nodes != catalog.nodes:
        raise CatalogError(f"catálogo {path}: sus nodos no son los del catálogo de arranque")
    return stored
