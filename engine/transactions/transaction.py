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
from typing import TYPE_CHECKING, Any

from engine.transactions.locks import DEFAULT_LOCK_TIMEOUT, LockManager, LockMode

if TYPE_CHECKING:
    # Import diferido (ver record_insert/record_delete): engine.executor ya
    # importa engine.transactions (processor.py necesita Transaction), asi
    # que importar engine.executor.dml al nivel de modulo aqui crearia un
    # ciclo que solo se nota si engine.transactions se importa antes que
    # engine.executor (por ejemplo, al correr engine/transactions/*.py en
    # aislamiento).
    from engine.executor.dml import DeleteCandidate


class Transaction:
    """Transaccion activa de un ``QueryProcessor``.

    Ademas de la bitacora de deshacer (#29), lleva la cuenta de los locks de
    tabla que fue adquiriendo: con 2PL estricto (ADR 0005), una transaccion
    explicita retiene cada lock desde su primer uso hasta que confirma o se
    deshace, y ambos caminos los liberan al final.
    """

    def __init__(self, database: Any, lock_manager: LockManager) -> None:
        self._database = database
        self._undo_actions: list[Callable[[], None]] = []
        self._lock_manager = lock_manager
        self._owner = object()
        self._locked_tables: dict[str, LockMode] = {}

    def ensure_lock(
        self,
        table_name: str,
        mode: LockMode,
        timeout: float = DEFAULT_LOCK_TIMEOUT,
    ) -> None:
        """Adquiere el lock de ``table_name`` si esta transaccion aun no lo tiene.

        Ya tener EXCLUSIVE cubre cualquier pedido posterior (de SHARED o de
        EXCLUSIVE); pedir EXCLUSIVE teniendo SHARED hace un upgrade.
        """

        current = self._locked_tables.get(table_name)
        if current is LockMode.EXCLUSIVE or current is mode:
            return
        self._lock_manager.acquire(table_name, self._owner, mode, timeout)
        self._locked_tables[table_name] = mode

    def record_insert(
        self,
        table_name: str,
        values: list[object],
        rid: Any,
        key_column: int,
        index_metadata: list[Any],
    ) -> None:
        """Registra como deshacer el INSERT que acaba de aplicarse."""

        from engine.executor.dml import DeleteCandidate, delete_with_indexes

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

        from engine.executor.dml import insert_with_indexes

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

    def commit(self) -> None:
        """Confirma: descarta la bitacora de deshacer y libera los locks.

        No hay nada mas que hacer para "confirmar" (ADR 0004): sin WAL, cada
        operacion ya quedo en disco en cuanto se aplico.
        """

        self._undo_actions.clear()
        self._release_locks()

    def rollback(self) -> None:
        """Deshace, en reversa, todo lo que esta transaccion llego a aplicar.

        De mejor esfuerzo, igual que el rollback de ``dml.py``: si deshacer
        una entrada falla, se sigue con el resto en vez de dejarlas sin
        intentar. Libera los locks al final, incluso si algun undo fallo.
        """

        for undo in reversed(self._undo_actions):
            with suppress(Exception):
                undo()
        self._undo_actions.clear()
        self._release_locks()

    def _release_locks(self) -> None:
        self._lock_manager.release_all(self._owner, self._locked_tables)
        self._locked_tables.clear()


__all__ = ["Transaction"]
