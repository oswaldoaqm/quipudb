"""Operaciones DML que coordinan una tabla con sus indices secundarios."""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from engine.executor.native import from_native_record
from engine.executor.predicates import equality_key, matches, range_values
from engine.planner.optimizer import AccessRoute, PhysicalDeletePlan


@dataclass(frozen=True, slots=True)
class DeleteCandidate:
    """Fila nativa copiada antes de comenzar a modificar sus estructuras."""

    rid: Any | None
    record: tuple[object, ...]


@dataclass(frozen=True, slots=True)
class _RemovedIndexEntry:
    candidate: DeleteCandidate
    index: Any
    key: object


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


def execute_delete(
    database: Any,
    native: Any,
    plan: PhysicalDeletePlan,
) -> int:
    """Materializa candidatos y los borra de la tabla y de cada indice."""

    candidates = collect_delete_candidates(database, native, plan)
    return delete_with_indexes(
        database,
        plan.statement.schema.table_name,
        candidates,
        plan.statement.schema.key_column,
        list(plan.table.indexes),
    )


def collect_delete_candidates(
    database: Any,
    native: Any,
    plan: PhysicalDeletePlan,
) -> tuple[DeleteCandidate, ...]:
    """Lee todos los candidatos, con RID cuando hay indices, antes de mutar."""

    statement = plan.statement
    condition = statement.where
    table_name = statement.schema.table_name
    table = database.table(table_name)

    if plan.route is AccessRoute.SCAN:
        if not plan.residual_filter:
            raise ValueError("un scan de DELETE necesita un filtro residual")
        candidates = tuple(
            DeleteCandidate(rid, tuple(record)) for rid, record in table.scan_with_rids()
        )
    elif plan.route is AccessRoute.TABLE_SEARCH:
        records = table.search(equality_key(condition, native))
        candidates = tuple(DeleteCandidate(None, tuple(record)) for record in records)
    elif plan.route is AccessRoute.TABLE_RANGE:
        lower, upper = range_values(condition, native)
        records = table.range_search(lower, upper)
        candidates = tuple(DeleteCandidate(None, tuple(record)) for record in records)
    else:
        candidates = _read_index_candidates(database, native, table, plan)

    if plan.route is AccessRoute.SCAN or plan.residual_filter:
        candidates = tuple(
            candidate
            for candidate in candidates
            if matches(from_native_record(candidate.record, statement.schema), condition)
        )
    return candidates


def _read_index_candidates(
    database: Any,
    native: Any,
    table: Any,
    plan: PhysicalDeletePlan,
) -> tuple[DeleteCandidate, ...]:
    metadata = plan.index
    if metadata is None:
        raise ValueError(f"la ruta {plan.route.value} necesita un indice")
    table_name = plan.statement.schema.table_name
    condition = plan.statement.where
    index = database.index(table_name, metadata.name)

    if plan.route is AccessRoute.INDEX_SEARCH:
        rids = index.search(equality_key(condition, native))
    elif plan.route is AccessRoute.INDEX_RANGE:
        if not index.supports_range():
            raise RuntimeError(
                f"el indice {metadata.name!r} fue planificado para rango, pero no lo soporta"
            )
        lower, upper = range_values(condition, native)
        rids = index.range_search(lower, upper)
    else:
        raise ValueError(f"ruta de DELETE desconocida: {plan.route!r}")

    candidates: list[DeleteCandidate] = []
    for rid in rids:
        if any(rid == candidate.rid for candidate in candidates):
            raise RuntimeError(
                f"el indice {metadata.name!r} de {table_name!r} devolvio el RID duplicado {rid!r}"
            )
        record = table.read(rid)
        if record is None:
            raise RuntimeError(
                f"el indice {metadata.name!r} de {table_name!r} "
                f"apunta a un RID inexistente: {rid!r}"
            )
        candidates.append(DeleteCandidate(rid, tuple(record)))
    return tuple(candidates)


def delete_with_indexes(
    database: Any,
    table_name: str,
    candidates: tuple[DeleteCandidate, ...],
    key_column: int,
    index_metadata: list[Any],
) -> int:
    """Borra filas materializadas y revierte cambios conocidos si algo falla.

    Primero se retiran todas las entradas secundarias y solo despues se borran
    las filas. Asi un fallo de indice no alcanza a modificar la tabla. Si una
    eliminacion de tabla falla a mitad del conjunto, las filas ya borradas se
    reinsertan y cada indice se reconstruye con el RID nuevo que corresponda.
    La reversion es de mejor esfuerzo y nunca oculta la excepcion original.
    """

    table = database.table(table_name)
    indexes = [(metadata, database.index(table_name, metadata.name)) for metadata in index_metadata]
    _validate_delete_candidates(candidates, key_column, index_metadata)

    removed_entries: list[_RemovedIndexEntry] = []
    removed_rows: list[DeleteCandidate] = []
    try:
        for candidate in candidates:
            for metadata, index in indexes:
                key = candidate.record[int(metadata.column)]
                if not index.remove_one(key, candidate.rid):
                    raise RuntimeError(
                        f"el indice {metadata.name!r} no contiene "
                        f"({key!r}, {candidate.rid!r})"
                    )
                removed_entries.append(_RemovedIndexEntry(candidate, index, key))

        for candidate in candidates:
            key = candidate.record[key_column]
            if table.remove(key) != 1:
                raise RuntimeError(
                    f"la fila candidata con clave primaria {key!r} desaparecio antes de borrarse"
                )
            removed_rows.append(candidate)
    except Exception:
        _rollback_delete(table, removed_entries, removed_rows)
        raise

    return len(candidates)


def _validate_delete_candidates(
    candidates: tuple[DeleteCandidate, ...],
    key_column: int,
    index_metadata: list[Any],
) -> None:
    primary_keys: list[object] = []
    rids: list[Any] = []
    required_columns = [key_column, *(int(metadata.column) for metadata in index_metadata)]

    for candidate in candidates:
        if any(column < 0 or column >= len(candidate.record) for column in required_columns):
            raise ValueError("un candidato de DELETE no coincide con el esquema de la tabla")
        key = candidate.record[key_column]
        if any(key == previous for previous in primary_keys):
            raise RuntimeError(f"DELETE recibio dos candidatos con la clave primaria {key!r}")
        primary_keys.append(key)

        if not index_metadata:
            continue
        if candidate.rid is None:
            raise ValueError("DELETE necesita el RID de cada candidato para mantener sus indices")
        if any(candidate.rid == previous for previous in rids):
            raise RuntimeError(f"DELETE recibio dos veces el RID {candidate.rid!r}")
        rids.append(candidate.rid)


def _rollback_delete(
    table: Any,
    removed_entries: list[_RemovedIndexEntry],
    removed_rows: list[DeleteCandidate],
) -> None:
    restored_rows: list[tuple[DeleteCandidate, Any]] = []
    for candidate in reversed(removed_rows):
        try:
            rid = table.insert(list(candidate.record))
        except Exception:
            continue
        restored_rows.append((candidate, rid))

    for entry in reversed(removed_entries):
        rid = _restored_rid(table, entry.candidate, removed_rows, restored_rows)
        if rid is None:
            continue
        with suppress(Exception):
            entry.index.insert(entry.key, rid)


def _restored_rid(
    table: Any,
    candidate: DeleteCandidate,
    removed_rows: list[DeleteCandidate],
    restored_rows: list[tuple[DeleteCandidate, Any]],
) -> Any | None:
    for restored_candidate, rid in restored_rows:
        if restored_candidate is candidate:
            return rid
    if any(removed_candidate is candidate for removed_candidate in removed_rows):
        return None
    try:
        record = table.read(candidate.rid)
    except Exception:
        return None
    return candidate.rid if record is not None else None


__all__ = [
    "DeleteCandidate",
    "collect_delete_candidates",
    "delete_with_indexes",
    "execute_delete",
    "insert_with_indexes",
]
