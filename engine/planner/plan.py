"""Estructura del plan de ejecucion (ADR 0002, issue #4).

Un plan es un arbol de pasos. Cada paso dice que operacion se hizo, sobre que
estructura, cuanto costo en paginas y registros, y cuanto tardo. El frontend
(Panel de Plan de Ejecucion, seccion 2.1.5) lo recibe como JSON tal cual lo
produce `Plan.to_dict()`; los benchmarks (2.1.6) leen los mismos contadores.

Los nombres de estructura son exactamente las constantes `kind::` del core
(`core/include/quipudb/catalog/table.hpp`) mas las que solo existen en la capa
Python. Los contadores son exactamente los campos de `OpStats`. Si algo cambia
en el core, cambia aqui en el mismo PR.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class Op(StrEnum):
    """Operaciones que puede contener un plan. Los hijos se ejecutan antes que
    el padre y le entregan su salida; un paso sin hijos lee directo del disco."""

    SCAN = "scan"  # recorrido completo de la tabla
    SEARCH = "search"  # busqueda puntual por clave primaria en la tabla
    RANGE_SEARCH = "range_search"  # busqueda por rango de clave primaria en la tabla
    INDEX_SEARCH = "index_search"  # busqueda puntual en un indice secundario: devuelve RIDs
    INDEX_RANGE = "index_range"  # busqueda por rango en un indice secundario: devuelve RIDs
    FETCH = "fetch"  # lee de la tabla los registros de los RIDs que entrega el hijo
    FILTER = "filter"  # evalua en memoria un predicado que ninguna estructura resolvio
    PROJECT = "project"  # se queda con algunas columnas
    SORT = "sort"  # ORDER BY con external sorting
    GROUP = "group"  # GROUP BY con external hashing
    JOIN = "join"  # JOIN con hashing externo o indice
    LIMIT = "limit"  # corta la salida
    INSERT = "insert"
    REMOVE = "remove"


class Structure(StrEnum):
    """Que estructura ejecuto el paso. Las cinco primeras son `kind::` del core."""

    HEAP = "heap"
    SEQUENTIAL = "sequential"
    BPLUS_CLUSTERED = "bplus_clustered"
    BPLUS_UNCLUSTERED = "bplus_unclustered"
    EXTENDIBLE_HASH = "extendible_hash"
    EXTERNAL_SORT = "external_sort"  # external/ del core: k-way merge
    EXTERNAL_HASH = "external_hash"  # external/ del core: particiones en disco
    MEMORY = "memory"  # el paso no toco disco (filter, project, limit)


@dataclass
class Stats:
    """Espejo de `OpStats` del core. Sumable, para agregar un subarbol."""

    pages_read: int = 0
    pages_written: int = 0
    records_examined: int = 0
    records_returned: int = 0

    def __add__(self, other: Stats) -> Stats:
        return Stats(
            self.pages_read + other.pages_read,
            self.pages_written + other.pages_written,
            self.records_examined + other.records_examined,
            self.records_returned + other.records_returned,
        )

    def to_dict(self) -> dict[str, int]:
        return {
            "pages_read": self.pages_read,
            "pages_written": self.pages_written,
            "records_examined": self.records_examined,
            "records_returned": self.records_returned,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Stats:
        return cls(**{k: int(d.get(k, 0)) for k in cls().to_dict()})


@dataclass
class Step:
    """Un nodo del plan.

    `stats` y `time_ms` son propios del paso, sin incluir a los hijos; el panel
    puede mostrar ambos niveles con `subtree_stats()` y `subtree_time_ms()`.
    `detail` es texto libre y legible para la persona (el predicado, la clave
    de orden, cuantos RIDs se resolvieron); no se parsea.
    """

    op: Op
    structure: Structure
    table: str | None = None
    column: str | None = None
    detail: str = ""
    stats: Stats = field(default_factory=Stats)
    time_ms: float = 0.0
    children: list[Step] = field(default_factory=list)

    def subtree_stats(self) -> Stats:
        total = self.stats
        for child in self.children:
            total = total + child.subtree_stats()
        return total

    def subtree_time_ms(self) -> float:
        return self.time_ms + sum(c.subtree_time_ms() for c in self.children)

    def walk(self) -> list[Step]:
        """Pasos en orden de ejecucion: primero los hijos, luego el padre."""
        out: list[Step] = []
        for child in self.children:
            out.extend(child.walk())
        out.append(self)
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "op": self.op.value,
            "structure": self.structure.value,
            "table": self.table,
            "column": self.column,
            "detail": self.detail,
            "stats": self.stats.to_dict(),
            "time_ms": round(self.time_ms, 3),
            "children": [c.to_dict() for c in self.children],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Step:
        return cls(
            op=Op(d["op"]),
            structure=Structure(d["structure"]),
            table=d.get("table"),
            column=d.get("column"),
            detail=d.get("detail", ""),
            stats=Stats.from_dict(d.get("stats", {})),
            time_ms=float(d.get("time_ms", 0.0)),
            children=[cls.from_dict(c) for c in d.get("children", [])],
        )


@dataclass
class Plan:
    """Lo que la API devuelve junto con los resultados de una consulta.

    `time_ms` es el tiempo total de la consulta medido por el planner, de
    parsear a devolver; es mayor o igual que `root.subtree_time_ms()` porque
    incluye parseo y planificacion.
    """

    query: str
    root: Step
    time_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "time_ms": round(self.time_ms, 3),
            "totals": self.root.subtree_stats().to_dict(),
            "root": self.root.to_dict(),
        }

    def to_json(self, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Plan:
        return cls(
            query=d["query"],
            root=Step.from_dict(d["root"]),
            time_ms=float(d.get("time_ms", 0.0)),
        )

    def structures_used(self) -> list[Structure]:
        """Estructuras que tocaron disco, en orden de ejecucion y sin repetir.
        Es lo que el panel resalta como 'indice usado'."""
        seen: list[Structure] = []
        for step in self.root.walk():
            if step.structure is not Structure.MEMORY and step.structure not in seen:
                seen.append(step.structure)
        return seen
