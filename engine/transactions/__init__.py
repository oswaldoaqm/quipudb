"""Transacciones de QuipuDB: agrupar operaciones como una sola unidad (2.1.4)."""

from engine.transactions.errors import TransactionError
from engine.transactions.transaction import Transaction

__all__ = ["Transaction", "TransactionError"]
