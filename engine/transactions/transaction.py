"""Bitacora de deshacer que agrupa las operaciones de una transaccion.

El core no tiene log de escritura anticipada (ADR 0001): cada INSERT/DELETE ya
queda en disco en cuanto se ejecuta (ADR 0004). Agrupar operaciones "como una
sola unidad" se resuelve aqui con una bitacora en memoria: cada operacion
exitosa registra como deshacerse, reutilizando las mismas primitivas atomicas
de ``engine.executor.dml`` que ya usa el ejecutor. Confirmar solo descarta la
bitacora; deshacer la reproduce en reversa.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from typing import Any

from engine.executor.dml import DeleteCandidate, delete_with_indexes, insert_with_indexes


class Transaction:
    """Transaccion activa de un ``QueryProcessor``."""

    def __init__(self, database: Any) -> None:
        self._database = database
        self._undo_actions: list[Callable[[], None]] = []

    def record_insert(
        self,
        table_name: str,
        values: list[object],
        rid: Any,
        key_column: int,
        index_metadata: list[Any],
    ) -> None:
        """Registra como deshacer el INSERT que acaba de aplicarse."""

        candidate = DeleteCandidate(rid=rid, record=tuple(values))

        def undo() -> None:
            delete_with_indexes(
                self._database, table_name, (candidate,), key_column, index_metadata
            )

        self._undo_actions.append(undo)

    def record_delete(
        self,
        table_name: str,
        candidates: tuple[DeleteCandidate, ...],
        key_column: int,
        index_metadata: list[Any],
    ) -> None:
        """Registra como deshacer el DELETE que acaba de aplicarse."""

        def undo() -> None:
            for candidate in candidates:
                insert_with_indexes(
                    self._database,
                    table_name,
                    list(candidate.record),
                    key_column,
                    index_metadata,
                )

        self._undo_actions.append(undo)

    def rollback(self) -> None:
        """Deshace, en reversa, todo lo que esta transaccion llego a aplicar.

        De mejor esfuerzo, igual que el rollback de ``dml.py``: si deshacer
        una entrada falla, se sigue con el resto en vez de dejarlas sin
        intentar.
        """

        for undo in reversed(self._undo_actions):
            with suppress(Exception):
                undo()
        self._undo_actions.clear()


__all__ = ["Transaction"]
