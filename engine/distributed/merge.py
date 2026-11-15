"""Combinación de los scans de una tabla ``range`` (#279).

Cada fragmento devuelve filas completas en orden físico. Antes de usarlas se
comprueba que el nodo respondió lo que el contrato promete: las columnas y los
tipos de la tabla, filas del ancho correcto y claves que pertenecen al
fragmento consultado. Si algo no cuadra la consulta falla con
``INVALID_NODE_RESPONSE``; nunca se descarta en silencio una fila sospechosa.

No hay deduplicación: los rangos de los fragmentos son disjuntos y cada uno se
consulta una sola vez, así que una fila solo puede llegar por un camino. Dos
filas con la misma clave serían un error del nodo y se rechazan; dos filas con
el mismo valor en otra columna son datos legítimos y se conservan.

Después se concatenan en el orden de los rangos, se ordenan por la clave si
hubo ORDER BY, se proyectan las columnas pedidas y se aplica el LIMIT global.

El plan de la respuesta (#293) describe eso mismo: los pasos del coordinador
(``sort``, ``project``, ``limit``, en memoria) y, debajo, el plan real que
devolvió cada nodo para su fragmento.
"""

from __future__ import annotations

from collections.abc import Sequence
from time import perf_counter

from engine.api.schemas import QueryResponse
from engine.distributed.client import NodeFailure
from engine.distributed.contracts import DistributedTable, Fragment
from engine.distributed.router import Scan
from engine.parser.ast import OrderDirection
from engine.planner.plan import Op, Plan, Stats, Step, Structure


def invalid(fragment: Fragment, message: str) -> NodeFailure:
    return NodeFailure(
        "INVALID_NODE_RESPONSE",
        message,
        502,
        "not_started",
        fragment.node_id,
        fragment.fragment_id,
    )


def _contains(fragment: Fragment, key: int) -> bool:
    return (fragment.lower is None or fragment.lower <= key) and (
        fragment.upper is None or key < fragment.upper
    )


def fragment_rows(
    table: DistributedTable, fragment: Fragment, result: QueryResponse
) -> list[list[object]]:
    """Las filas de un scan, después de comprobar que son de ``fragment``."""

    columns = [c.name for c in table.columns]
    types = [c.type for c in table.columns]
    if list(result.columns) != columns or [str(t) for t in result.column_types] != types:
        raise invalid(fragment, f"el scan no devolvió las columnas de {table.name}")
    if result.is_explain:
        raise invalid(fragment, "el scan devolvió un EXPLAIN")
    key = columns.index(table.primary_key)
    seen: set[int] = set()
    for row in result.rows:
        if len(row) != len(columns):
            raise invalid(fragment, "el scan devolvió una fila de otro ancho")
        value = row[key]
        if type(value) is not int or not _contains(fragment, value):
            raise invalid(fragment, f"la clave {value!r} no pertenece al fragmento")
        if value in seen:
            raise invalid(fragment, f"la clave {value} llegó dos veces")
        seen.add(value)
    return [list(row) for row in result.rows]


def merge(
    scan: Scan,
    results: Sequence[tuple[Fragment, QueryResponse]],
    *,
    sql: str = "",
    started: float | None = None,
) -> QueryResponse:
    """La respuesta pública de un SELECT distribuido.

    ``results`` va en el orden de ``scan.fragments``. Con cero fragmentos sale
    una tabla vacía con sus columnas y tipos, sin haber contactado a nadie.
    ``sql`` es la consulta del usuario y ``started`` el ``perf_counter`` de
    cuando llegó: los dos van al plan.
    """

    table = scan.table
    key = [c.name for c in table.columns].index(table.primary_key)
    columns = [table.columns[i].name for i in scan.projection]
    rows: list[list[object]] = []
    children: list[Step] = []
    for fragment, result in results:
        own = fragment_rows(table, fragment, result)
        rows.extend(own)
        children.append(fragment_step(table, fragment, result, len(own)))

    # Los pasos del coordinador, en el orden en que se aplican. El de más
    # abajo recibe los planes de los fragmentos como hijos.
    steps: list[Step] = []

    def step(op: Op, detail: str, before: int, after: int, begun: float) -> None:
        steps.append(
            Step(
                op,
                Structure.MEMORY,
                table.name,
                None,
                detail,
                Stats(records_examined=before, records_returned=after),
                (perf_counter() - begun) * 1000,
                children=[steps[-1]] if steps else children,
            )
        )

    if scan.order is not None:
        begun = perf_counter()
        rows.sort(key=lambda row: row[key], reverse=scan.order is OrderDirection.DESC)
        step(
            Op.SORT,
            f"{table.primary_key} {scan.order.value}; ordena en el coordinador las filas de "
            f"{_fragments(len(results))}",
            len(rows),
            len(rows),
            begun,
        )

    begun = perf_counter()
    total = len(rows)
    rows = [[row[i] for i in scan.projection] for row in rows]
    detail = ", ".join(columns)
    if not steps:
        detail += (
            f"; combina {_fragments(len(results))} en el coordinador"
            if results
            else "; ningún fragmento puede tener filas: no se consultó ningún nodo"
        )
    step(Op.PROJECT, detail, total, total, begun)

    if scan.limit is not None:
        begun = perf_counter()
        rows = rows[: scan.limit]
        step(Op.LIMIT, f"primeras {scan.limit} filas del total combinado", total, len(rows), begun)

    elapsed = (perf_counter() - started) * 1000 if started is not None else 0.0
    return QueryResponse(
        columns=columns,
        column_types=[table.columns[i].type for i in scan.projection],
        rows=rows,
        affected_rows=0,
        plan=Plan(sql, steps[-1], elapsed).to_dict(),
        spatial_context=None,
        is_explain=False,
    )


def _fragments(count: int) -> str:
    return "1 fragmento" if count == 1 else f"{count} fragmentos"


def fragment_step(
    table: DistributedTable, fragment: Fragment, result: QueryResponse, rows: int
) -> Step:
    """El plan real del scan de un fragmento, con su nodo a la vista.

    Es el plan que devolvió el nodo, sin recalcular nada: páginas, registros y
    tiempo son los que midió su motor. Solo se antepone al detalle de la raíz
    en qué nodo y fragmento corrió.
    """

    where = f"{fragment.node_id} · {fragment.fragment_id}"
    if result.plan is None or "root" not in result.plan:
        return Step(
            Op.SCAN,
            Structure(table.storage),
            table.name,
            None,
            f"{where}: el nodo no devolvió su plan",
            Stats(records_returned=rows),
        )
    root = Step.from_dict(result.plan["root"])
    root.detail = f"{where}: {root.detail}" if root.detail else where
    return root


__all__ = ["fragment_rows", "fragment_step", "invalid", "merge"]
