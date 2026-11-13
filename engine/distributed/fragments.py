"""Servicio local de nodo. Serializa validación y ejecución sobre un único motor."""

from pathlib import Path
from threading import RLock

from engine.api.service import describe_catalog, to_response
from engine.distributed.admission import validate_sql
from engine.distributed.node_errors import NodeError, sql_error
from engine.distributed.node_lease import NodeLease
from engine.distributed.node_requests import resolve_fragment, validate_request
from engine.distributed.protocol import (
    CatalogResponse,
    ExecutionResponse,
    FragmentInfo,
    HealthResponse,
)
from engine.distributed.storage import _close, audit_node_storage
from engine.executor import QueryProcessor
from engine.executor.native import load_native
from engine.parser.errors import SQLError


class FragmentService:
    def __init__(
        self,
        catalog,
        node_id: str,
        data_dir: str | Path,
        *,
        media_root: str | Path | None = None,
        media_vocabularies=None,
    ):
        catalog.node(node_id)  # Error de configuración: no arrancar con otra identidad.
        self.catalog = catalog
        self.node_id = node_id
        self.directory = Path(data_dir).resolve()
        self.media_root = media_root or self.directory / "multimedia"
        self.media_vocabularies = media_vocabularies or {}
        self._lock = RLock()
        self._lease = NodeLease(self.directory)
        self._database = None
        self._processor = None
        self._native = None
        self._failure = NodeError("NODE_NOT_READY", "el nodo no está iniciado")

    def start(self):
        with self._lock:
            if self._database is not None:
                return
            try:
                path = self.directory / "catalogo.txt"
                if not path.is_file() or path.is_symlink():
                    raise NodeError("SCHEMA_MISMATCH", "falta el catálogo físico local")
                try:
                    self._lease.acquire()
                except (OSError, ValueError) as error:
                    raise NodeError(
                        "NODE_NOT_READY", "el directorio no está disponible en exclusiva"
                    ) from error
                self._native = load_native()
                self._database = self._native.Database(path)
                self._audit(check_records=True)
                self._processor = QueryProcessor(
                    self._database,
                    media_root=self.media_root,
                    media_vocabularies=self.media_vocabularies,
                )
                self._failure = None
            except Exception as error:  # noqa: BLE001 -- salud debe informar fallos de arranque
                self._failure = (
                    error
                    if isinstance(error, NodeError)
                    else NodeError(
                        "NODE_NOT_READY",
                        str(error),
                    )
                )
                self._release()

    def _release(self):
        try:
            if self._database is not None:
                _close(self._database)
        finally:
            self._processor = None
            self._database = None
            self._lease.close()

    def close(self):
        with self._lock:
            self._failure = NodeError("NODE_NOT_READY", "el nodo está cerrado")
            self._release()

    def _audit(self, *, check_records=False):
        try:
            return audit_node_storage(
                self.catalog,
                self.node_id,
                self._database,
                self._native,
                self.directory,
                check_records=check_records,
            )
        except Exception as error:
            self._failure = NodeError("SCHEMA_MISMATCH", str(error))
            raise self._failure from error

    def _ready(self):
        if self._failure is not None:
            raise self._failure

    def health(self) -> HealthResponse:
        with self._lock:
            return HealthResponse(
                protocol_version=self.catalog.protocol_version,
                catalog_version=self.catalog.catalog_version,
                node_id=self.node_id,
                status="ready" if self._failure is None else "not_ready",
                fragment_ids=[f.fragment_id for f in self.catalog.fragments_for_node(self.node_id)],
                reason=str(self._failure) if self._failure else None,
            )

    def describe(self, data: dict) -> CatalogResponse:
        with self._lock:
            request = validate_request(data, self.catalog)
            self._ready()
            self._audit()
            descriptions = {t.name: t for t in describe_catalog(self._database, self._native)}
            fragments = [
                FragmentInfo(fragment_id=f.fragment_id, table_info=descriptions[t.name])
                for t in self.catalog.tables
                for f in t.fragments
                if f.node_id == self.node_id
            ]
            return CatalogResponse(
                protocol_version=self.catalog.protocol_version,
                catalog_version=self.catalog.catalog_version,
                request_id=request.request_id,
                node_id=self.node_id,
                fragments=fragments,
            )

    def execute(self, fragment_id: str, data: dict, *, scan=False) -> ExecutionResponse:
        with self._lock:
            request = validate_request(data, self.catalog, fragment=True)
            table, fragment = resolve_fragment(
                self.catalog, self.node_id, fragment_id, request.table
            )
            self._ready()
            self._audit()
            validate_sql(table, fragment, request.sql, scan=scan)
            try:
                result = to_response(self._processor.execute(request.sql))
            except SQLError as error:
                raise sql_error(error, state="unknown", native=self._native) from error
            except Exception as error:
                # Una escritura puede haber tenido efectos. No anunciar rollback
                # ni aceptar más peticiones hasta auditar/reiniciar el proceso.
                self._failure = NodeError("NODE_NOT_READY", "reiniciar tras fallo de ejecución")
                raise NodeError("EXECUTION_ERROR", str(error), state="unknown") from error
            return ExecutionResponse(
                protocol_version=self.catalog.protocol_version,
                catalog_version=self.catalog.catalog_version,
                request_id=request.request_id,
                node_id=self.node_id,
                fragment_id=fragment_id,
                result=result,
            )
