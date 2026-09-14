"""Operaciones DML que coordinan una tabla con sus indices secundarios."""

from __future__ import annotations

from contextlib import suppress
from typing import Any


def insert_with_indexes(
    database: Any,
    table_name: str,
    values: list[object],
    key_column: int,
    index_metadata: list[Any],
) -> Any:
    """Inserta una fila y mantiene todos los indices registrados.

    Los handles se abren antes de modificar la tabla. Si una escritura de
    indice falla, se quitan las entradas que pudieron agregarse y despues la
    fila. La reversion es de mejor esfuerzo: siempre se vuelve a lanzar el
    error original.
    """

    table = database.table(table_name)
    indexes = [(metadata, database.index(table_name, metadata.name)) for metadata in index_metadata]

    rid = table.insert(values)
    attempted: list[tuple[Any, Any, object]] = []
    try:
        for metadata, index in indexes:
            key = values[int(metadata.column)]
            attempted.append((metadata, index, key))
            index.insert(key, rid)
    except Exception:
        for _metadata, index, key in reversed(attempted):
            with suppress(Exception):
                index.remove_one(key, rid)
        with suppress(Exception):
            table.remove(values[key_column])
        raise

    return rid
