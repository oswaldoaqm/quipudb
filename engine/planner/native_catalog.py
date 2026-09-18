"""Adaptador del catalogo nativo al modelo puro del optimizador."""

from __future__ import annotations

from typing import Any

from engine.planner.optimizer import IndexMetadata, TableMetadata
from engine.planner.plan import Structure

_TABLE_STRUCTURES = {
    Structure.HEAP,
    Structure.SEQUENTIAL,
    Structure.BPLUS_CLUSTERED,
}
_INDEX_STRUCTURES = {
    Structure.BPLUS_UNCLUSTERED,
    Structure.EXTENDIBLE_HASH,
}


def from_native_table_info(table_info: Any, rows: int | None = None) -> TableMetadata:
    """Copia un ``TableInfo`` de pybind11 sin conservar referencias nativas.

    ``TableInfo`` no lleva el conteo de filas: sale de ``TableFile::size()``, que
    exige abrir la tabla. Quien lo tenga lo pasa; quien no, deja ``None`` y el
    join cae a hash join, que es la respuesta segura.
    """

    table_structure = _structure(table_info.storage, _TABLE_STRUCTURES, "tabla")
    table_name = str(table_info.schema.table_name)
    column_count = len(table_info.schema.columns)

    indexes: list[IndexMetadata] = []
    for native_index in table_info.indexes:
        structure = _structure(native_index.kind, _INDEX_STRUCTURES, "indice")
        column = int(native_index.column)
        if not 0 <= column < column_count:
            raise ValueError(
                f"el indice {native_index.name!r} referencia la columna inexistente {column}"
            )
        indexes.append(
            IndexMetadata(
                name=str(native_index.name),
                column=column,
                structure=structure,
                supports_range=structure is Structure.BPLUS_UNCLUSTERED,
            )
        )

    return TableMetadata(
        name=table_name,
        structure=table_structure,
        indexes=tuple(indexes),
        rows=rows,
    )


def load_table_metadata(database: Any, table_name: str) -> TableMetadata:
    """Obtiene y desacopla la metadata de una tabla registrada, con su tamano.

    Abre la tabla para preguntarle ``size()``. ``Database`` devuelve siempre el
    mismo handle, asi que no es una apertura de mas: es la que el ejecutor iba
    a hacer igual.
    """

    return from_native_table_info(
        database.table_info(table_name),
        rows=int(database.table(table_name).size()),
    )


def _structure(value: Any, allowed: set[Structure], role: str) -> Structure:
    try:
        structure = Structure(str(value))
    except ValueError as error:
        raise ValueError(f"estructura nativa desconocida para {role}: {value!r}") from error
    if structure not in allowed:
        raise ValueError(f"{structure.value} no es una estructura de {role}")
    return structure


__all__ = ["from_native_table_info", "load_table_metadata"]
