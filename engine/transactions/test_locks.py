"""Pruebas de `TableLock`/`LockManager` con hilos reales, sin bindings.

No hace falta compilar el core para probar el mecanismo de bloqueo en si: es
codigo Python puro sobre `threading`. Las pruebas de concurrencia contra el
core real (`engine/test_concurrency.py`) construyen sobre esta base ya
probada.
"""

from __future__ import annotations

import threading
import time

import pytest

from engine.transactions.errors import LockTimeoutError
from engine.transactions.locks import LockManager, LockMode, TableLock


def test_locks_compartidos_conviven_al_mismo_tiempo() -> None:
    lock = TableLock("alumnos")
    both_arrived = threading.Barrier(2, timeout=1)

    def reader() -> None:
        owner = object()
        lock.acquire(owner, LockMode.SHARED, timeout=1)
        both_arrived.wait(timeout=1)
        lock.release(owner)

    threads = [threading.Thread(target=reader) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=1)
        assert not thread.is_alive()


def test_exclusivo_excluye_a_un_lector() -> None:
    lock = TableLock("alumnos")
    writer = object()
    reader = object()
    lock.acquire(writer, LockMode.EXCLUSIVE, timeout=1)
    reader_got_lock = threading.Event()

    def read() -> None:
        lock.acquire(reader, LockMode.SHARED, timeout=1)
        reader_got_lock.set()

    thread = threading.Thread(target=read)
    thread.start()
    assert not reader_got_lock.wait(timeout=0.2)

    lock.release(writer)

    assert reader_got_lock.wait(timeout=1)
    thread.join(timeout=1)


def test_exclusivo_excluye_a_otro_escritor() -> None:
    lock = TableLock("alumnos")
    first = object()
    second = object()
    lock.acquire(first, LockMode.EXCLUSIVE, timeout=1)

    with pytest.raises(LockTimeoutError):
        lock.acquire(second, LockMode.EXCLUSIVE, timeout=0.1)


def test_upgrade_de_compartido_a_exclusivo_sin_otros_lectores() -> None:
    lock = TableLock("alumnos")
    owner = object()
    lock.acquire(owner, LockMode.SHARED, timeout=1)

    lock.acquire(owner, LockMode.EXCLUSIVE, timeout=1)

    other = object()
    with pytest.raises(LockTimeoutError):
        lock.acquire(other, LockMode.SHARED, timeout=0.1)


def test_mismo_owner_repite_un_lock_sin_bloquearse() -> None:
    lock = TableLock("alumnos")
    owner = object()
    lock.acquire(owner, LockMode.EXCLUSIVE, timeout=1)

    lock.acquire(owner, LockMode.EXCLUSIVE, timeout=1)
    lock.acquire(owner, LockMode.SHARED, timeout=1)


def test_timeout_lanza_lock_timeout_error_dentro_del_plazo() -> None:
    lock = TableLock("alumnos")
    lock.acquire(object(), LockMode.EXCLUSIVE, timeout=1)

    start = time.monotonic()
    with pytest.raises(LockTimeoutError):
        lock.acquire(object(), LockMode.EXCLUSIVE, timeout=0.1)
    elapsed = time.monotonic() - start

    assert 0.1 <= elapsed < 1.0


def test_release_libera_para_el_siguiente_en_espera() -> None:
    lock = TableLock("alumnos")
    owner = object()
    lock.acquire(owner, LockMode.EXCLUSIVE, timeout=1)
    lock.release(owner)

    other = object()
    lock.acquire(other, LockMode.EXCLUSIVE, timeout=0.2)


def test_manager_aisla_locks_por_tabla() -> None:
    manager = LockManager()
    owner = object()
    manager.acquire("alumnos", owner, LockMode.EXCLUSIVE, timeout=1)

    other = object()
    manager.acquire("cursos", other, LockMode.EXCLUSIVE, timeout=0.2)


def test_manager_release_all_libera_varias_tablas_de_una() -> None:
    manager = LockManager()
    owner = object()
    manager.acquire("alumnos", owner, LockMode.EXCLUSIVE, timeout=1)
    manager.acquire("cursos", owner, LockMode.EXCLUSIVE, timeout=1)

    manager.release_all(owner, ["alumnos", "cursos"])

    other = object()
    manager.acquire("alumnos", other, LockMode.EXCLUSIVE, timeout=0.2)
    manager.acquire("cursos", other, LockMode.EXCLUSIVE, timeout=0.2)
