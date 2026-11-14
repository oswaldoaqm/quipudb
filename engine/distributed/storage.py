"""Preparación y auditoría fuera de línea de nodos range para #276.

No es un servicio de operaciones: no recibe SQL ni enruta INSERT/DELETE.
Debe usarse sin procesos abiertos sobre los directorios involucrados.
"""

from pathlib import Path

from engine.api.service import describe_catalog
from engine.distributed.catalog import DistributedCatalog
from engine.distributed.partitioning import owner_for_key
from engine.executor.native import load_native, to_native_schema


def _tables(catalog: DistributedCatalog, node_id: str):
    catalog.node(node_id)
    tables = tuple(
        table for table in catalog.tables if any(f.node_id == node_id for f in table.fragments)
    )
    if not tables or any(table.mode != "range" for table in tables):
        raise ValueError("esta utilidad prepara/audita nodos con tablas range exclusivamente")
    return tables


def _close(database) -> None:
    try:
        database.flush()
    finally:
        for name in database.open_tables():
            database.close(name)


def prepare_range_node(
    catalog: DistributedCatalog,
    node_id: str,
    data_dir: str | Path,
) -> Path:
    """Crea archivos reales en un directorio NUEVO y devuelve catalogo.txt.

    Rechaza directorios existentes incluso vacíos: dos nodos no pueden adoptar
    accidentalmente el mismo almacenamiento. No borra restos si falla el core.
    IVF/HNSW requieren entrenamiento/configuración que no contiene este catálogo;
    se rechazan antes de crear archivos, sin inventar parámetros por omisión.
    """
    tables = _tables(catalog, node_id)
    for table in tables:
        if any(
            i.structure not in {"bplus_unclustered", "extendible_hash", "rtree"}
            for i in table.indexes
        ):
            raise ValueError("preparación automática admite B+, hash y R-Tree; no IVF/HNSW")
    native = load_native()
    directory = Path(data_dir).absolute()
    directory.mkdir(parents=True, exist_ok=False)
    catalog_path = directory / "catalogo.txt"
    database = native.Database(catalog_path)
    try:
        for table in tables:
            database.create_table(to_native_schema(table.bound_schema, native), table.storage)
            for index in table.indexes:
                database.create_index(table.name, index.name, index.column, index.structure)
    finally:
        _close(database)
    return catalog_path


def verify_range_node(
    catalog: DistributedCatalog,
    node_id: str,
    data_dir: str | Path,
) -> dict[str, int]:
    """Reabre, compara metadatos y recorre PK reales; devuelve conteos por fragmento.

    No modifica registros ni repara datos incorrectos. No sustituye los locks
    ni las validaciones por petición que implementará #277.
    """
    _tables(catalog, node_id)
    directory = Path(data_dir).resolve()
    catalog_path = directory / "catalogo.txt"
    if not catalog_path.is_file() or catalog_path.is_symlink():
        raise ValueError("se requiere un catálogo físico local existente")
    native = load_native()
    database = native.Database(catalog_path)
    try:
        return audit_node_storage(catalog, node_id, database, native, directory)
    finally:
        _close(database)


def audit_node_storage(
    catalog: DistributedCatalog,
    node_id: str,
    database,
    native,
    directory: Path,
    *,
    check_records: bool = True,
) -> dict[str, int]:
    """Audita el MISMO handle del servicio; admite tablas range y single.

    El llamador posee la exclusión mutua. No abre otra Database ni la cierra.
    El recorrido de pertenencia completo es para arranque; por petición se
    comparan metadatos sin volver a recorrer toda la tabla.
    """
    catalog.node(node_id)
    tables = tuple(t for t in catalog.tables if any(f.node_id == node_id for f in t.fragments))
    directory = directory.resolve()
    if set(database.table_names()) != {t.name for t in tables}:
        raise ValueError("las tablas físicas no coinciden con el catálogo distribuido")
    for table in tables:
        info = database.table_info(table.name)
        for filename in (info.file, *(i.file for i in info.indexes)):
            path = directory / filename
            if path.is_symlink() or path.resolve().parent != directory or not path.is_file():
                raise ValueError("archivo físico fuera del directorio propio del nodo")
    actual = {t.name: t for t in describe_catalog(database, native)}
    counts = {}
    for table in tables:
        physical = actual[table.name]
        expected_columns = [
            {
                "name": c.name,
                "type": c.type,
                "size": c.size,
                "is_primary_key": c.name == table.primary_key,
            }
            for c in table.columns
        ]
        expected_indexes = sorted((i.model_dump() for i in table.indexes), key=lambda i: i["name"])
        if (
            physical.storage != table.storage
            or [c.model_dump() for c in physical.columns] != expected_columns
            or sorted((i.model_dump() for i in physical.indexes), key=lambda i: i["name"])
            != expected_indexes
        ):
            raise ValueError(f"esquema/índices físicos incompatibles: {table.name}")
        fragment = next(f for f in table.fragments if f.node_id == node_id)
        count = 0
        rows = native.source_of(database.table(table.name)) if check_records else ()
        for row in rows:
            if (
                table.mode == "range"
                and owner_for_key(table, row[table.bound_schema.key_column]) != fragment
            ):
                raise ValueError(f"registro fuera del fragmento {fragment.fragment_id}")
            count += 1
        counts[fragment.fragment_id] = count if check_records else physical.record_count
    return counts
