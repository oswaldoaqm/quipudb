"""Nodos falsos para probar el cliente y el coordinador sin motor ni red.

Responden con ``httpx.MockTransport``: cada prueba decide qué devuelve cada
ruta, incluidas respuestas rotas, de otro nodo o fallos de transporte.
"""

from __future__ import annotations

import json

import httpx


def table_info(table, record_count: int = 0) -> dict:
    """El ``TableInfo`` que describiría un nodo correcto para ``table``."""

    return {
        "name": table.name,
        "storage": table.storage,
        "columns": [
            {
                "name": c.name,
                "type": c.type,
                "size": c.size,
                "is_primary_key": c.name == table.primary_key,
            }
            for c in table.columns
        ],
        "indexes": [i.model_dump() for i in table.indexes],
        "record_count": record_count,
    }


class FakeNode:
    """Un nodo que responde como dice el contrato, salvo lo que se cambie.

    ``counts`` da el ``record_count`` de cada fragmento y ``rows`` las filas
    que devuelve su scan. ``override`` es una función
    ``(request, body) -> httpx.Response | None`` que puede reemplazar la
    respuesta de cualquier petición; ``requests`` guarda todo lo recibido.
    """

    def __init__(self, catalog, node_id: str, counts=None, override=None, rows=None) -> None:
        self.catalog = catalog
        self.node_id = node_id
        self.counts = counts or {}
        self.override = override
        self.rows = rows or {}
        self.requests: list[tuple[httpx.Request, dict | None]] = []

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def base(self, **extra) -> dict:
        return {
            "protocol_version": self.catalog.protocol_version,
            "catalog_version": self.catalog.catalog_version,
            "node_id": self.node_id,
            **extra,
        }

    def fragments(self):
        return [
            (table, fragment)
            for table in self.catalog.tables
            for fragment in table.fragments
            if fragment.node_id == self.node_id
        ]

    def handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        self.requests.append((request, body))
        if self.override is not None:
            replaced = self.override(request, body)
            if replaced is not None:
                return replaced
        path = request.url.path
        if path.endswith("/health"):
            ids = [f.fragment_id for _, f in self.fragments()]
            return httpx.Response(
                200, json=self.base(status="ready", fragment_ids=ids, reason=None)
            )
        if path.endswith("/catalog"):
            return httpx.Response(
                200,
                json=self.base(
                    request_id=body["request_id"],
                    fragments=[
                        {
                            "fragment_id": f.fragment_id,
                            "table_info": table_info(t, self.counts.get(f.fragment_id, 0)),
                        }
                        for t, f in self.fragments()
                    ],
                ),
            )
        fragment_id = path.split("/")[-2]
        result = {
            "columns": [],
            "column_types": [],
            "rows": [],
            "affected_rows": 1,
            "plan": None,
            "spatial_context": None,
            "is_explain": False,
        }
        if path.endswith("/scan"):
            table = self.catalog.table(body["table"])
            result.update(
                columns=[c.name for c in table.columns],
                column_types=[c.type for c in table.columns],
                rows=self.rows.get(fragment_id, []),
                affected_rows=0,
            )
        return httpx.Response(
            200,
            json=self.base(request_id=body["request_id"], fragment_id=fragment_id, result=result),
        )


def error_body(node: FakeNode, body: dict | None, code: str, state="not_started", **sql) -> dict:
    """El sobre de error v1 que mandaría ``node``."""

    return node.base(
        request_id=body.get("request_id") if body else None,
        fragment_id=None,
        error={
            "code": code,
            "message": f"falla {code}",
            "execution_state": state,
            "kind": sql.get("kind"),
            "line": sql.get("line"),
            "column": sql.get("column"),
            "end_line": sql.get("end_line"),
            "end_column": sql.get("end_column"),
        },
    )
