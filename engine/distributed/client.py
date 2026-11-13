"""Cliente HTTP del coordinador hacia un nodo, según el contrato v1 (#278).

Un ``NodeClient`` por nodo del catálogo. Cada llamada genera un ``request_id``
nuevo, envía el sobre v1 y valida la respuesta antes de devolverla: versiones,
identidad del nodo, ``request_id`` y, en las rutas de fragmento, el
``fragment_id`` esperado. Nada que no pase esas pruebas se trata como éxito.

Los fallos se informan siempre como ``NodeFailure``, con el código del contrato:

- los que devuelve el nodo (``SQL_ERROR``, ``WRONG_FRAGMENT``…) conservan su
  código, su estado HTTP, ``execution_state`` y la ubicación SQL;
- los que genera el cliente son ``NODE_UNAVAILABLE`` (no se pudo conectar),
  ``NODE_TIMEOUT`` (no respondió a tiempo) e ``INVALID_NODE_RESPONSE``
  (respuesta ilegible, de otro nodo o cortada a la mitad).

No hay reintentos ni redirecciones. Si una escritura alcanzó a enviarse y no
llegó una respuesta válida, su estado es ``unknown``: pudo haberse aplicado.
Solo cuando es seguro que nada salió del coordinador el estado es
``not_started``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal, TypeVar
from uuid import uuid4

import httpx
from pydantic import ValidationError

from engine.distributed.catalog import DistributedCatalog
from engine.distributed.contracts import Node
from engine.distributed.protocol import (
    CatalogResponse,
    ErrorResponse,
    ExecutionResponse,
    HealthResponse,
    Response,
)

PREFIX = "/internal/v1"

DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=3.0, pool=3.0)
"""Los valores iniciales del contrato: 3 s para conectar y para obtener una
conexión del pool, 30 s para leer o escribir."""

PUBLIC_STATUS = {
    "NODE_UNAVAILABLE": 503,
    "NODE_TIMEOUT": 504,
    "INVALID_NODE_RESPONSE": 502,
}
"""Estado HTTP público de los fallos que genera el cliente. Los errores que
devuelve un nodo conservan el estado que mandó el nodo."""

ExecutionState = Literal["not_started", "unknown"]
R = TypeVar("R", bound=Response)


@dataclass(eq=False)
class NodeFailure(Exception):
    """Un fallo al hablar con un nodo, listo para traducirse a la API pública."""

    code: str
    message: str
    status: int
    execution_state: ExecutionState
    node_id: str
    fragment_id: str | None = None
    write: bool = False
    kind: str | None = None
    line: int | None = None
    column: int | None = None
    end_line: int | None = None
    end_column: int | None = None

    def __str__(self) -> str:
        return self.public_message()

    def public_message(self) -> str:
        """El texto de ``QueryErrorResponse.error``: código, origen y detalle."""

        origen = f"nodo {self.node_id}"
        if self.fragment_id is not None:
            origen += f", fragmento {self.fragment_id}"
        texto = f"[{self.code}] {origen}: {self.message}"
        if self.write and self.execution_state == "unknown":
            texto += "; resultado de escritura incierto; no se reintentó"
        return texto


class NodeClient:
    """Habla con un nodo por HTTP/JSON. Uno por nodo; se cierra con ``aclose``."""

    def __init__(
        self,
        catalog: DistributedCatalog,
        node: Node,
        *,
        timeout: httpx.Timeout = DEFAULT_TIMEOUT,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.catalog = catalog
        self.node = node
        self._http = httpx.AsyncClient(
            base_url=node.base_url,
            timeout=timeout,
            follow_redirects=False,
            transport=transport,
        )

    @property
    def node_id(self) -> str:
        return self.node.node_id

    async def aclose(self) -> None:
        await self._http.aclose()

    # -- rutas del contrato ---------------------------------------------------

    async def health(self) -> HealthResponse:
        """``GET /health``. Un nodo ``not_ready`` responde 503 con el mismo
        cuerpo: se devuelve igual, no es un fallo de comunicación."""

        response = await self._send("GET", f"{PREFIX}/health", None, write=False, fragment=None)
        if response.status_code not in (200, 503):
            raise self._invalid(f"salud respondió HTTP {response.status_code}", write=False)
        health = self._parse(response, HealthResponse, write=False, fragment=None)
        self._check_versions(health, write=False, fragment=None)
        if (response.status_code == 200) != (health.status == "ready"):
            raise self._invalid("el estado HTTP de salud no coincide con su cuerpo", write=False)
        return health

    async def describe(self) -> CatalogResponse:
        """``POST /catalog``: los fragmentos que aloja el nodo y sus conteos."""

        request_id, response = await self._post(f"{PREFIX}/catalog", {}, write=False)
        result = self._result(response, CatalogResponse, request_id, write=False, fragment=None)
        return result

    async def execute(
        self, fragment_id: str, table: str, sql: str, *, write: bool
    ) -> ExecutionResponse:
        """``POST /fragments/{id}/execute``. ``write`` marca INSERT/DELETE, para
        saber si un fallo después de enviar deja el resultado incierto."""

        return await self._fragment("execute", fragment_id, table, sql, write=write)

    async def scan(self, fragment_id: str, table: str, sql: str) -> ExecutionResponse:
        """``POST /fragments/{id}/scan``: filas completas de un fragmento range."""

        return await self._fragment("scan", fragment_id, table, sql, write=False)

    # -- transporte y validación ----------------------------------------------

    async def _fragment(
        self, route: str, fragment_id: str, table: str, sql: str, *, write: bool
    ) -> ExecutionResponse:
        request_id, response = await self._post(
            f"{PREFIX}/fragments/{fragment_id}/{route}",
            {"table": table, "sql": sql},
            write=write,
            fragment=fragment_id,
        )
        result = self._result(
            response, ExecutionResponse, request_id, write=write, fragment=fragment_id
        )
        if result.fragment_id != fragment_id:
            raise self._invalid(
                f"respondió el fragmento {result.fragment_id}", write=write, fragment=fragment_id
            )
        return result

    async def _post(
        self, path: str, extra: dict, *, write: bool, fragment: str | None = None
    ) -> tuple[str, httpx.Response]:
        request_id = str(uuid4())
        body = {
            "protocol_version": self.catalog.protocol_version,
            "catalog_version": self.catalog.catalog_version,
            "request_id": request_id,
            **extra,
        }
        response = await self._send("POST", path, body, write=write, fragment=fragment)
        return request_id, response

    async def _send(
        self, method: str, path: str, body: dict | None, *, write: bool, fragment: str | None
    ) -> httpx.Response:
        try:
            if body is None:
                return await self._http.request(method, path)
            return await self._http.request(method, path, json=body)
        except (httpx.ConnectError, httpx.ConnectTimeout) as error:
            # No se estableció la conexión: la petición no salió.
            raise self._failure(
                "NODE_UNAVAILABLE", f"no se pudo conectar: {error}", "not_started", write, fragment
            ) from error
        except httpx.PoolTimeout as error:
            raise self._failure(
                "NODE_TIMEOUT", "no hubo conexión libre a tiempo", "not_started", write, fragment
            ) from error
        except httpx.TimeoutException as error:
            raise self._failure(
                "NODE_TIMEOUT", "el nodo no respondió a tiempo", "unknown", write, fragment
            ) from error
        except httpx.HTTPError as error:
            # Desconexión o protocolo roto después de enviar el cuerpo.
            raise self._failure(
                "INVALID_NODE_RESPONSE",
                f"la conexión se cortó: {error}",
                "unknown",
                write,
                fragment,
            ) from error

    def _result(
        self,
        response: httpx.Response,
        model: type[R],
        request_id: str,
        *,
        write: bool,
        fragment: str | None,
    ) -> R:
        if response.status_code != 200:
            raise self._node_error(response, request_id, write=write, fragment=fragment)
        result = self._parse(response, model, write=write, fragment=fragment)
        self._check_versions(result, write=write, fragment=fragment)
        if result.request_id != request_id:
            raise self._invalid("respondió a otro request_id", write=write, fragment=fragment)
        return result

    def _node_error(
        self, response: httpx.Response, request_id: str, *, write: bool, fragment: str | None
    ) -> NodeFailure:
        body = self._parse(response, ErrorResponse, write=write, fragment=fragment)
        if body.node_id != self.node_id:
            return self._invalid(
                f"respondió el nodo {body.node_id}", write=write, fragment=fragment
            )
        if body.request_id not in (None, request_id):
            return self._invalid("respondió a otro request_id", write=write, fragment=fragment)
        detail = body.error
        return NodeFailure(
            code=detail.code,
            message=detail.message,
            status=response.status_code,
            execution_state=detail.execution_state,
            node_id=self.node_id,
            fragment_id=body.fragment_id or fragment,
            write=write,
            kind=detail.kind,
            line=detail.line,
            column=detail.column,
            end_line=detail.end_line,
            end_column=detail.end_column,
        )

    def _parse(
        self, response: httpx.Response, model: type[R], *, write: bool, fragment: str | None
    ) -> R:
        try:
            return model.model_validate(response.json())
        except (json.JSONDecodeError, UnicodeError, ValidationError) as error:
            raise self._invalid(
                f"respuesta HTTP {response.status_code} ilegible: {error}",
                write=write,
                fragment=fragment,
            ) from error

    def _check_versions(self, result: Response, *, write: bool, fragment: str | None) -> None:
        if result.node_id != self.node_id:
            raise self._invalid(
                f"respondió el nodo {result.node_id}", write=write, fragment=fragment
            )
        if result.catalog_version != self.catalog.catalog_version:
            raise self._invalid(
                f"usa catalog_version {result.catalog_version} y el coordinador "
                f"{self.catalog.catalog_version}",
                write=write,
                fragment=fragment,
            )

    def _invalid(self, message: str, *, write: bool, fragment: str | None = None) -> NodeFailure:
        # Llegó algo, pero no una respuesta válida: si era una escritura, el
        # nodo pudo haberla aplicado.
        return self._failure(
            "INVALID_NODE_RESPONSE", message, "unknown" if write else "not_started", write, fragment
        )

    def _failure(
        self,
        code: str,
        message: str,
        state: ExecutionState,
        write: bool,
        fragment: str | None,
    ) -> NodeFailure:
        return NodeFailure(
            code=code,
            message=message,
            status=PUBLIC_STATUS[code],
            execution_state=state,
            node_id=self.node_id,
            fragment_id=fragment,
            write=write,
        )
