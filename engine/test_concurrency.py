"""Pruebas de concurrencia real del lock manager contra el core compilado.

Complementa `engine/transactions/test_locks.py` (que prueba `TableLock` en
aislamiento): aqui varios hilos, cada uno con su propio `QueryProcessor`,
comparten una misma `Database` y un mismo `LockManager` (ADR 0005) — el
mismo arreglo que usara la demo de hilos del #32.
"""

from __future__ import annotations

import threading

import pytest

from engine.executor import QueryProcessor
from engine.transactions import LockManager, LockTimeoutError

quipudb = pytest.importorskip(
    "quipudb_native",
    reason="los bindings no estan compilados: cmake -DQUIPUDB_BUILD_PYTHON=ON",
)


def test_inserts_concurrentes_con_claves_distintas_no_se_pierden(tmp_path) -> None:
    db = quipudb.Database(tmp_path / "catalogo.txt")
    QueryProcessor(db).execute("CREATE TABLE alumnos (id INT PRIMARY KEY, hilo INT) USING HEAP")

    lock_manager = LockManager()
    thread_count = 8
    inserts_per_thread = 25
    errors: list[BaseException] = []

    def worker(thread_id: int) -> None:
        processor = QueryProcessor(db, lock_manager=lock_manager)
        try:
            for offset in range(inserts_per_thread):
                key = thread_id * inserts_per_thread + offset
                processor.execute(f"INSERT INTO alumnos VALUES ({key}, {thread_id})")
        except BaseException as error:  # noqa: BLE001 - se reporta en el hilo principal
            errors.append(error)

    threads = [threading.Thread(target=worker, args=(thread_id,)) for thread_id in range(thread_count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
        assert not thread.is_alive()

    assert not errors
    assert len(db.table("alumnos").scan()) == thread_count * inserts_per_thread


def test_transacciones_concurrentes_no_pierden_actualizaciones_sobre_la_misma_fila(
    tmp_path,
) -> None:
    """Modela la "actualizacion perdida" del material del curso.

    Sin locks, dos transacciones que leen la misma fila, calculan un nuevo
    valor y lo escriben pueden pisarse: la segunda escritura sobrevive y la
    primera se pierde. Con locks de tabla y 2PL estricto (ADR 0005), cada
    incremento se aplica exactamente una vez. Cuando dos transacciones
    compiten por el mismo upgrade compartido->exclusivo (el interbloqueo que
    muestra el material), el timeout aborta una de las dos automaticamente y
    el hilo reintenta el ciclo completo.
    """

    db = quipudb.Database(tmp_path / "catalogo.txt")
    QueryProcessor(db).execute("CREATE TABLE contador (id INT PRIMARY KEY, valor INT) USING HEAP")
    QueryProcessor(db).execute("INSERT INTO contador VALUES (1, 0)")

    lock_manager = LockManager()
    thread_count = 3
    increments_per_thread = 4
    errors: list[BaseException] = []

    def increment_once(processor: QueryProcessor) -> None:
        while True:
            try:
                processor.execute("BEGIN TRANSACTION")
                (row,) = processor.execute("SELECT valor FROM contador WHERE id = 1").rows
                nuevo_valor = row[0] + 1
                processor.execute("DELETE FROM contador WHERE id = 1")
                processor.execute(f"INSERT INTO contador VALUES (1, {nuevo_valor})")
                processor.execute("END TRANSACTION")
                return
            except LockTimeoutError:
                # La transaccion ya se aborto y libero sus locks
                # (`_fail_in_transaction`); se reintenta el ciclo completo.
                continue

    def worker() -> None:
        processor = QueryProcessor(db, lock_manager=lock_manager, lock_timeout=0.2)
        try:
            for _ in range(increments_per_thread):
                increment_once(processor)
        except BaseException as error:  # noqa: BLE001 - se reporta en el hilo principal
            errors.append(error)

    threads = [threading.Thread(target=worker) for _ in range(thread_count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
        assert not thread.is_alive()

    assert not errors
    (final_row,) = QueryProcessor(db).execute("SELECT valor FROM contador WHERE id = 1").rows
    assert final_row[0] == thread_count * increments_per_thread
