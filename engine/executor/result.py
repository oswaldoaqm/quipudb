"""Resultado publico de la ejecucion de una sentencia SQL."""

from __future__ import annotations

from dataclasses import dataclass

from engine.planner.plan import Plan


@dataclass(frozen=True, slots=True)
class QueryResult:
    """Filas, metadatos y plan producidos por el ejecutor.

    Las sentencias que no retornan filas dejan ``columns`` y ``rows`` vacios y
    comunican su efecto mediante ``affected_rows``.
    """

    columns: tuple[str, ...] = ()
    rows: tuple[tuple[object, ...], ...] = ()
    affected_rows: int = 0
    plan: Plan | None = None

    def __post_init__(self) -> None:
        # El core devuelve listas de pybind11. Copiarlas a tuplas evita que el
        # resultado publico cambie si el llamador conserva y muta esas listas.
        object.__setattr__(self, "columns", tuple(self.columns))
        object.__setattr__(self, "rows", tuple(tuple(row) for row in self.rows))

        if self.affected_rows < 0:
            raise ValueError("affected_rows no puede ser negativo")

        expected_width = len(self.columns)
        for index, row in enumerate(self.rows):
            if len(row) != expected_width:
                raise ValueError(
                    f"la fila {index} tiene {len(row)} valores; se esperaban {expected_width}"
                )
