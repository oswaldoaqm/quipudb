"""Lock manager de tabla: control de concurrencia pesimista (2.1.4).

El core no es seguro para hilos a proposito (ver `core/include/quipudb/
catalog/database.hpp`): sincronizar el acceso concurrente es trabajo de esta
capa. Sigue el mismo modelo de estado que el "Concurrency Manager" del
material del curso (`material/05 Recuperación ante Fallos.pdf`, diapositivas
42-61): cada tabla tiene un estado `libre` / `compartido` (con lectores) /
`exclusivo`, y quien no puede tomar el lock espera hasta que se libere.

Dos diferencias con el algoritmo del material: el lock aqui es por `owner`
(no anonimo), para poder ser reentrante y soportar upgrade
compartido->exclusivo sin que un dueno se bloquee a si mismo; y `acquire`
tiene un `timeout` en vez de esperar indefinidamente, que es la estrategia de
interbloqueo elegida (ver ADR 0005) en vez de deteccion por grafo de espera.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterable
from enum import StrEnum

from engine.transactions.errors import LockTimeoutError

DEFAULT_LOCK_TIMEOUT = 5.0


class LockMode(StrEnum):
    SHARED = "SHARED"
    EXCLUSIVE = "EXCLUSIVE"


class TableLock:
    """Lock compartido/exclusivo de una sola tabla."""

    def __init__(self, table_name: str) -> None:
        self._table_name = table_name
        self._condition = threading.Condition()
        self._mode: LockMode | None = None
        self._owners: set[object] = set()

    def acquire(self, owner: object, mode: LockMode, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        with self._condition:
            while not self._try_acquire_locked(owner, mode):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise LockTimeoutError(
                        f"tiempo de espera agotado pidiendo {mode.value} "
                        f"sobre la tabla {self._table_name!r}"
                    )
                self._condition.wait(remaining)

    def _try_acquire_locked(self, owner: object, mode: LockMode) -> bool:
        if owner in self._owners:
            if mode is LockMode.SHARED or self._mode is LockMode.EXCLUSIVE:
                return True
            if len(self._owners) == 1:
                self._mode = LockMode.EXCLUSIVE
                return True
            return False

        if self._mode is None:
            self._owners.add(owner)
            self._mode = mode
            return True
        if self._mode is LockMode.SHARED and mode is LockMode.SHARED:
            self._owners.add(owner)
            return True
        return False

    def release(self, owner: object) -> None:
        with self._condition:
            self._owners.discard(owner)
            if not self._owners:
                self._mode = None
            self._condition.notify_all()


class LockManager:
    """Un `TableLock` por tabla, creado perezosamente."""

    def __init__(self) -> None:
        self._locks: dict[str, TableLock] = {}
        self._creation_lock = threading.Lock()

    def _lock_for(self, table_name: str) -> TableLock:
        with self._creation_lock:
            lock = self._locks.get(table_name)
            if lock is None:
                lock = TableLock(table_name)
                self._locks[table_name] = lock
            return lock

    def acquire(
        self,
        table_name: str,
        owner: object,
        mode: LockMode,
        timeout: float = DEFAULT_LOCK_TIMEOUT,
    ) -> None:
        self._lock_for(table_name).acquire(owner, mode, timeout)

    def release(self, table_name: str, owner: object) -> None:
        lock = self._locks.get(table_name)
        if lock is not None:
            lock.release(owner)

    def release_all(self, owner: object, table_names: Iterable[str]) -> None:
        for table_name in table_names:
            self.release(table_name, owner)


__all__ = ["DEFAULT_LOCK_TIMEOUT", "LockManager", "LockMode", "TableLock"]
