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
"""

from __future__ import annotations

from collections.abc import Sequence

from engine.api.schemas import QueryResponse
from engine.distributed.client import NodeFailure
from engine.distributed.contracts import DistributedTable, Fragment
from engine.distributed.router import Scan
from engine.parser.ast import OrderDirection


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


def merge(scan: Scan, results: Sequence[tuple[Fragment, QueryResponse]]) -> QueryResponse:
    """La respuesta pública de un SELECT distribuido.

    ``results`` va en el orden de ``scan.fragments``. Con cero fragmentos sale
    una tabla vacía con sus columnas y tipos, sin haber contactado a nadie.
    """

    table = scan.table
    rows: list[list[object]] = []
    for fragment, result in results:
        rows.extend(fragment_rows(table, fragment, result))

    if scan.order is not None:
        key = [c.name for c in table.columns].index(table.primary_key)
        rows.sort(key=lambda row: row[key], reverse=scan.order is OrderDirection.DESC)

    rows = [[row[i] for i in scan.projection] for row in rows]
    if scan.limit is not None:
        rows = rows[: scan.limit]

    return QueryResponse(
        columns=[table.columns[i].name for i in scan.projection],
        column_types=[table.columns[i].type for i in scan.projection],
        rows=rows,
        affected_rows=0,
        plan=None,
        spatial_context=None,
        is_explain=False,
    )


__all__ = ["fragment_rows", "invalid", "merge"]
