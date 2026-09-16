"""Errores de secuenciamiento de transacciones."""

from __future__ import annotations


class TransactionError(Exception):
    """La secuencia de BEGIN/END TRANSACTION (o un rollback) no es valida.

    Distinta de ``SQLError``: no es un problema de sintaxis o semantica de una
    sentencia, sino del orden en que se piden las operaciones de transaccion
    (por ejemplo, un BEGIN anidado o un END sin BEGIN previo).
    """


class LockTimeoutError(TransactionError):
    """Un ``acquire`` de lock no se pudo cumplir dentro del plazo dado.

    Es la estrategia de interbloqueo elegida (ver ADR 0005): en vez de
    detectar ciclos en un grafo de espera, cada intento de tomar un lock
    tiene un limite de tiempo. Al ser subclase de ``TransactionError``, un
    timeout dentro de una transaccion activa dispara el mismo camino de
    aborto automatico que ya usan los errores de dominio.
    """


__all__ = ["LockTimeoutError", "TransactionError"]
