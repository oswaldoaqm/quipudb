"""Pruebas de la bitacora de deshacer, en aislamiento y sin bindings nativos.

``Transaction`` delega en ``engine.executor.dml.insert_with_indexes`` y
``delete_with_indexes``, que ya estan probadas contra el core real en
``engine/executor/test_delete.py``. Aqui interesa solo la bitacora: que
registre la operacion inversa correcta y que la reproduzca en el orden y con
la tolerancia a fallos esperados. Un doble minimo de ``Database``/``TableFile``
alcanza porque las pruebas usan ``index_metadata=[]`` (sin indices no hay
llamadas a ``database.index``).
"""

from __future__ import annotations

import pytest

from engine.executor.dml import DeleteCandidate
from engine.transactions.errors import LockTimeoutError
from engine.transactions.locks import LockManager, LockMode
from engine.transactions.transaction import Transaction


class FakeTable:
    def __init__(self) -> None:
        self.rows: dict[object, list[object]] = {}

    def insert(self, record: list[object]) -> object:
        key = record[0]
        if key in self.rows:
            raise ValueError(f"clave duplicada: {key!r}")
        self.rows[key] = list(record)
        return key

    def remove(self, key: object) -> int:
        return 1 if self.rows.pop(key, None) is not None else 0


class FakeDatabase:
    def __init__(self) -> None:
        self.table_ = FakeTable()

    def table(self, name: str) -> FakeTable:
        del name
        return self.table_


def test_rollback_deshace_un_insert_registrado() -> None:
    database = FakeDatabase()
    rid = database.table_.insert([1, "ada"])
    transaction = Transaction(database, LockManager())
    transaction.record_insert("alumnos", [1, "ada"], rid, key_column=0, index_metadata=[])

    transaction.rollback()

    assert database.table_.rows == {}


def test_rollback_deshace_un_delete_registrado() -> None:
    database = FakeDatabase()
    database.table_.insert([1, "ada"])
    candidate = DeleteCandidate(rid=0, record=(1, "ada"))
    database.table_.remove(1)
    transaction = Transaction(database, LockManager())
    transaction.record_delete("alumnos", (candidate,), key_column=0, index_metadata=[])

    transaction.rollback()

    assert database.table_.rows == {1: [1, "ada"]}


def test_rollback_deshace_en_orden_lifo_cuando_una_clave_se_reutiliza() -> None:
    database = FakeDatabase()
    database.table_.insert([1, "ada"])
    original = DeleteCandidate(rid=0, record=(1, "ada"))
    database.table_.remove(1)

    transaction = Transaction(database, LockManager())
    transaction.record_delete("alumnos", (original,), key_column=0, index_metadata=[])
    new_rid = database.table_.insert([1, "bob"])
    transaction.record_insert("alumnos", [1, "bob"], new_rid, key_column=0, index_metadata=[])

    transaction.rollback()

    # Si el rollback deshiciera en FIFO, intentaria reinsertar a "ada" mientras
    # "bob" sigue ocupando la clave 1: ese undo fallaria (silenciosamente) y la
    # tabla quedaria vacia en vez de con "ada" de vuelta.
    assert database.table_.rows == {1: [1, "ada"]}


def test_rollback_no_reproduce_dos_veces_la_misma_bitacora() -> None:
    database = FakeDatabase()
    rid = database.table_.insert([1, "ada"])
    transaction = Transaction(database, LockManager())
    transaction.record_insert("alumnos", [1, "ada"], rid, key_column=0, index_metadata=[])

    transaction.rollback()
    transaction.rollback()

    assert database.table_.rows == {}


def test_rollback_es_de_mejor_esfuerzo_y_no_se_detiene_en_el_primer_fallo() -> None:
    database = FakeDatabase()
    rid_ada = database.table_.insert([1, "ada"])
    rid_bob = database.table_.insert([2, "bob"])
    transaction = Transaction(database, LockManager())
    transaction.record_insert("alumnos", [1, "ada"], rid_ada, key_column=0, index_metadata=[])
    transaction.record_insert("alumnos", [2, "bob"], rid_bob, key_column=0, index_metadata=[])
    database.table_.remove(2)  # "bob" desaparece por fuera de la transaccion

    transaction.rollback()

    # El undo de "bob" (va primero, LIFO) falla porque ya no esta; el de "ada"
    # se ejecuta de todos modos.
    assert database.table_.rows == {}


def test_ensure_lock_adquiere_una_sola_vez_por_tabla() -> None:
    lock_manager = LockManager()
    transaction = Transaction(FakeDatabase(), lock_manager)

    transaction.ensure_lock("alumnos", LockMode.SHARED)
    transaction.ensure_lock("alumnos", LockMode.SHARED)

    # Si `ensure_lock` hubiera vuelto a pedir el lock, este intento desde otro
    # owner en modo exclusivo se quedaria esperando y agotaria el timeout.
    other_owner = object()
    with pytest.raises(LockTimeoutError):
        lock_manager.acquire("alumnos", other_owner, LockMode.EXCLUSIVE, timeout=0.05)


def test_ensure_lock_hace_upgrade_de_compartido_a_exclusivo() -> None:
    lock_manager = LockManager()
    transaction = Transaction(FakeDatabase(), lock_manager)

    transaction.ensure_lock("alumnos", LockMode.SHARED)
    transaction.ensure_lock("alumnos", LockMode.EXCLUSIVE)

    other_owner = object()
    with pytest.raises(LockTimeoutError):
        lock_manager.acquire("alumnos", other_owner, LockMode.SHARED, timeout=0.05)


def test_commit_libera_los_locks_sin_deshacer() -> None:
    lock_manager = LockManager()
    database = FakeDatabase()
    rid = database.table_.insert([1, "ada"])
    transaction = Transaction(database, lock_manager)
    transaction.record_insert("alumnos", [1, "ada"], rid, key_column=0, index_metadata=[])
    transaction.ensure_lock("alumnos", LockMode.EXCLUSIVE)

    transaction.commit()

    assert database.table_.rows == {1: [1, "ada"]}
    other_owner = object()
    lock_manager.acquire("alumnos", other_owner, LockMode.EXCLUSIVE, timeout=0.05)


def test_rollback_libera_los_locks() -> None:
    lock_manager = LockManager()
    database = FakeDatabase()
    rid = database.table_.insert([1, "ada"])
    transaction = Transaction(database, lock_manager)
    transaction.record_insert("alumnos", [1, "ada"], rid, key_column=0, index_metadata=[])
    transaction.ensure_lock("alumnos", LockMode.EXCLUSIVE)

    transaction.rollback()

    assert database.table_.rows == {}
    other_owner = object()
    lock_manager.acquire("alumnos", other_owner, LockMode.EXCLUSIVE, timeout=0.05)
