"""Ejecucion publica del subconjunto SQL de QuipuDB."""

from engine.executor.processor import QueryProcessor
from engine.executor.result import QueryResult

__all__ = ["QueryProcessor", "QueryResult"]
