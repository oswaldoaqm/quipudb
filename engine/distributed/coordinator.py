"""Coordinador distribuido de QuipuDB (#278).

Es el único proceso que ve el catálogo distribuido entero. Tiene un
``NodeClient`` por nodo y nunca abre archivos de datos: todo lo que sabe de un
nodo lo pregunta por HTTP.

Lo que hace en esta etapa:

- ``check_nodes``: pregunta salud y catálogo a cada nodo y lo compara con la
  configuración estática. Un nodo está listo solo si responde, dice estar
  listo, aloja exactamente los fragmentos que el catálogo le asigna y cada uno
  tiene el esquema, la organización y los índices declarados.
- ``tables``: la vista lógica de ``GET /tables``. Cada tabla aparece una vez;
  en una tabla ``range`` se suman los conteos de sus fragmentos. Si falta un
  nodo necesario se devuelve error, nunca un conteo incompleto.

El enrutamiento de ``POST /query`` y la combinación de resultados son del #279.

Uso, con los nodos ya levantados (``engine.distributed.node_server``)::

    python -m engine.distributed.coordinator --catalog catalogo.json --catalog-version 1

Escucha en el puerto 8000, el mismo de la API local, para que el frontend se
conecte al coordinador sin cambiar su configuración.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, replace
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from engine.api.schemas import QueryErrorResponse, QueryRequest, QueryResponse, TableInfo
from engine.api.service import to_error
from engine.distributed.catalog import DistributedCatalog, load_catalog
from engine.distributed.client import DEFAULT_TIMEOUT, NodeClient, NodeFailure
from engine.distributed.contracts import DistributedTable
from engine.distributed.merge import merge
from engine.distributed.protocol import CatalogResponse
from engine.distributed.router import Execute, Scan, route
from engine.parser.errors import SQLError

_LOGGER = logging.getLogger(__name__)

ORIGENES = ("http://localhost:5173", "http://127.0.0.1:5173")
"""Los mismos orígenes del frontend que acepta la API local."""


@dataclass(frozen=True)
class NodeStatus:
    """Lo que el coordinador sabe de un nodo después de revisarlo."""

    node_id: str
    base_url: str
    ready: bool
    reason: str | None
    fragment_ids: tuple[str, ...]


def schema_problem(table: DistributedTable, info: TableInfo) -> str | None:
    """Qué no coincide entre la tabla declarada y la que describe un nodo.

    Compara nombre, organización, columnas en orden (tipo, tamaño y clave) e
    índices por nombre. Devuelve ``None`` si todo coincide.
    """

    if info.name != table.name:
        return f"se esperaba la tabla {table.name} y el nodo describe {info.name}"
    if info.storage != table.storage:
        return f"{table.name} se almacena como {info.storage}, no como {table.storage}"
    expected = [(c.name, c.type, c.size, c.name == table.primary_key) for c in table.columns]
    actual = [(c.name, c.type.value, c.size, c.is_primary_key) for c in info.columns]
    if actual != expected:
        return f"las columnas de {table.name} no coinciden con el catálogo"
    declared = sorted((i.name, i.column, i.structure, i.supports_range) for i in table.indexes)
    present = sorted((i.name, i.column, str(i.structure), i.supports_range) for i in info.indexes)
    if present != declared:
        return f"los índices de {table.name} no coinciden con el catálogo"
    return None


class Coordinator:
    """Clientes de todos los nodos y las operaciones que los combinan."""

    def __init__(
        self,
        catalog: DistributedCatalog,
        *,
        timeout: httpx.Timeout = DEFAULT_TIMEOUT,
        transports: dict[str, httpx.AsyncBaseTransport] | None = None,
    ) -> None:
        transports = transports or {}
        self.catalog = catalog
        self.clients = {
            node.node_id: NodeClient(
                catalog, node, timeout=timeout, transport=transports.get(node.node_id)
            )
            for node in catalog.nodes
        }

    def client(self, node_id: str) -> NodeClient:
        return self.clients[node_id]

    async def aclose(self) -> None:
        await asyncio.gather(*(c.aclose() for c in self.clients.values()))

    # -- salud ----------------------------------------------------------------

    async def check_nodes(self) -> list[NodeStatus]:
        """Revisa todos los nodos en paralelo. No lanza: un nodo caído es un
        estado, no un error del coordinador."""

        return list(await asyncio.gather(*(self._check(n) for n in self.clients)))

    async def _check(self, node_id: str) -> NodeStatus:
        client = self.clients[node_id]
        expected = sorted(f.fragment_id for f in self.catalog.fragments_for_node(node_id))

        def status(ready: bool, reason: str | None, fragments=()) -> NodeStatus:
            return NodeStatus(node_id, client.node.base_url, ready, reason, tuple(fragments))

        try:
            health = await client.health()
        except NodeFailure as failure:
            return status(False, failure.public_message())
        if health.status != "ready":
            return status(False, health.reason or "el nodo no está listo", health.fragment_ids)
        if sorted(health.fragment_ids) != expected:
            return status(
                False,
                f"aloja {sorted(health.fragment_ids)} y el catálogo le asigna {expected}",
                health.fragment_ids,
            )
        try:
            description = await client.describe()
        except NodeFailure as failure:
            return status(False, failure.public_message(), health.fragment_ids)
        problem = self._description_problem(node_id, description)
        return status(problem is None, problem, health.fragment_ids)

    def _description_problem(self, node_id: str, description: CatalogResponse) -> str | None:
        described = {f.fragment_id: f.table_info for f in description.fragments}
        expected = {f.fragment_id for f in self.catalog.fragments_for_node(node_id)}
        if set(described) != expected:
            return f"describe {sorted(described)} y el catálogo le asigna {sorted(expected)}"
        for table in self.catalog.tables:
            for fragment in table.fragments:
                if fragment.node_id == node_id:
                    problem = schema_problem(table, described[fragment.fragment_id])
                    if problem is not None:
                        return f"fragmento {fragment.fragment_id}: {problem}"
        return None

    # -- vista lógica ---------------------------------------------------------

    async def describe_nodes(self, node_ids) -> dict[str, CatalogResponse]:
        """``POST /catalog`` a cada nodo pedido, en paralelo. Si uno falla, falla
        todo: no se arma una vista con nodos faltantes."""

        node_ids = sorted(set(node_ids))
        results = await asyncio.gather(
            *(self.clients[n].describe() for n in node_ids), return_exceptions=True
        )
        for result in results:
            if isinstance(result, BaseException):
                raise result
        return dict(zip(node_ids, results, strict=True))

    async def tables(self) -> list[TableInfo]:
        """``GET /tables`` del clúster: una entrada por tabla lógica."""

        needed = {f.node_id for t in self.catalog.tables for f in t.fragments}
        descriptions = await self.describe_nodes(needed)
        infos: dict[str, tuple[str, TableInfo]] = {}
        for node_id, description in descriptions.items():
            problem = self._description_problem(node_id, description)
            if problem is not None:
                raise NodeFailure("SCHEMA_MISMATCH", problem, 409, "not_started", node_id)
            for fragment in description.fragments:
                infos[fragment.fragment_id] = (node_id, fragment.table_info)

        result = []
        for table in self.catalog.tables:
            parts = [infos[f.fragment_id][1] for f in table.ordered_fragments]
            if table.mode == "single":
                result.append(parts[0])
            else:
                total = sum(p.record_count for p in parts)
                result.append(parts[0].model_copy(update={"record_count": total}))
        return result

    # -- consultas --------------------------------------------------------------

    async def query(self, sql: str) -> QueryResponse:
        """``POST /query`` del clúster.

        Lanza ``SQLError`` si la sentencia no es válida o v1 no la admite, y
        ``NodeFailure`` si falla un nodo. Una lectura con varios fragmentos
        falla entera si falla uno: nunca hay filas parciales.
        """

        target = route(self.catalog, sql)
        if isinstance(target, Execute):
            response = await self.clients[target.node_id].execute(
                target.fragment_id, target.table, target.sql, write=target.write
            )
            return response.result
        return await self._scan(target)

    async def _scan(self, scan: Scan) -> QueryResponse:
        results = await asyncio.gather(
            *(
                self.clients[f.node_id].scan(f.fragment_id, scan.table.name, scan.sql)
                for f in scan.fragments
            ),
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, NodeFailure):
                # El nodo leyó un SQL generado aquí, no el del usuario: sus
                # posiciones no corresponden a la consulta pública.
                raise replace(result, line=None, column=None, end_line=None, end_column=None)
            if isinstance(result, BaseException):
                raise result
        return merge(
            scan,
            [(f, r.result) for f, r in zip(scan.fragments, results, strict=True)],
        )


def failure_response(failure: NodeFailure) -> JSONResponse:
    """Un ``NodeFailure`` como ``QueryErrorResponse``, el error público de siempre."""

    body = QueryErrorResponse(
        error=failure.public_message(),
        kind=failure.kind,
        line=failure.line,
        column=failure.column,
        end_line=failure.end_line,
        end_column=failure.end_column,
    )
    return JSONResponse(status_code=failure.status, content=body.model_dump())


def create_coordinator_app(coordinator: Coordinator) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI):
        # Los nodos pueden levantarse después que el coordinador: se informa su
        # estado al arrancar, pero no se impide arrancar.
        for status in await coordinator.check_nodes():
            if status.ready:
                _LOGGER.info("nodo %s listo en %s", status.node_id, status.base_url)
            else:
                _LOGGER.warning("nodo %s no está listo: %s", status.node_id, status.reason)
        try:
            yield
        finally:
            await coordinator.aclose()

    app = FastAPI(title="QuipuDB coordinador distribuido v1", lifespan=lifespan)
    app.state.coordinator = coordinator
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(ORIGENES),
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

    @app.exception_handler(NodeFailure)
    async def _node_failure(_, failure: NodeFailure) -> JSONResponse:
        return failure_response(failure)

    @app.exception_handler(SQLError)
    async def _sql_error(_, error: SQLError) -> JSONResponse:
        return JSONResponse(status_code=400, content=to_error(error).model_dump())

    @app.get("/health")
    async def health() -> JSONResponse:
        """Estado del clúster: 200 si todos los nodos están listos, 503 si no."""

        statuses = await coordinator.check_nodes()
        ready = all(s.ready for s in statuses)
        body = {
            "status": "ready" if ready else "degraded",
            "catalog_version": coordinator.catalog.catalog_version,
            "nodes": [asdict(s) for s in statuses],
        }
        return JSONResponse(status_code=200 if ready else 503, content=body)

    @app.get("/tables", response_model=list[TableInfo])
    async def tables() -> list[TableInfo]:
        return await coordinator.tables()

    @app.post("/query", response_model=QueryResponse)
    async def query(request: QueryRequest) -> QueryResponse:
        return await coordinator.query(request.sql)

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Coordinador distribuido de QuipuDB")
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--catalog-version", type=int, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    import uvicorn

    catalog = load_catalog(args.catalog, expected_catalog_version=args.catalog_version)
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(create_coordinator_app(Coordinator(catalog)), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
