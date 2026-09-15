"""Errores de secuenciamiento de transacciones."""

from __future__ import annotations


class TransactionError(Exception):
    """La secuencia de BEGIN/END TRANSACTION (o un rollback) no es valida.

    Distinta de ``SQLError``: no es un problema de sintaxis o semantica de una
    sentencia, sino del orden en que se piden las operaciones de transaccion
    (por ejemplo, un BEGIN anidado o un END sin BEGIN previo).
    """


__all__ = ["TransactionError"]
