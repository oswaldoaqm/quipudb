"""Medicion aislada de operaciones nativas y pasos en memoria."""

from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter_ns
from typing import Any, Callable, Generic, TypeVar

from engine.planner.plan import Stats

_T = TypeVar("_T")


@dataclass(frozen=True, slots=True)
class Measurement(Generic[_T]):
    """Resultado, contadores propios y tiempo propio de una operacion."""

    value: _T
    stats: Stats
    time_ms: float


def measure_native(owner: Any, operation: Callable[[], _T]) -> Measurement[_T]:
    """Resetea y copia los contadores de una unica operacion del core.

    ``stats()`` devuelve contadores acumulados. Resetear inmediatamente antes
    de cada llamada evita que una consulta herede trabajo de la anterior y que
    el paso ``fetch`` absorba las lecturas hechas por el indice.
    """

    owner.reset_stats()
    start = perf_counter_ns()
    value = operation()
    elapsed_ms = (perf_counter_ns() - start) / 1_000_000
    return Measurement(value, copy_stats(owner.stats()), elapsed_ms)


def measure_memory(
    operation: Callable[[], _T],
    *,
    records_examined: int,
    records_returned: Callable[[_T], int],
) -> Measurement[_T]:
    """Mide un operador puro y construye sus contadores sin paginas."""

    start = perf_counter_ns()
    value = operation()
    elapsed_ms = (perf_counter_ns() - start) / 1_000_000
    return Measurement(
        value,
        Stats(
            records_examined=records_examined,
            records_returned=records_returned(value),
        ),
        elapsed_ms,
    )


def copy_stats(native_stats: Any) -> Stats:
    """Desacopla un ``OpStats`` mutable del handle nativo."""

    return Stats(
        pages_read=int(native_stats.pages_read),
        pages_written=int(native_stats.pages_written),
        records_examined=int(native_stats.records_examined),
        records_returned=int(native_stats.records_returned),
    )


__all__ = ["Measurement", "copy_stats", "measure_memory", "measure_native"]
