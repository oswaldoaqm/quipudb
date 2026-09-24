"""API HTTP del motor: lo unico que el frontend (2.1.5) consume.

`POST /query` y `GET /tables`, con la forma que fija
`frontend/src/api/types.ts`, y `POST /tables/{tabla}/load` para cargar un CSV. Levantar con:

    uvicorn engine.api.main:app --reload --port 8000

El catalogo sale de la variable de entorno ``QUIPUDB_CATALOG``; por defecto,
``datos/catalogo.txt`` relativo a donde se levante el servidor.
"""

from __future__ import annotations

import io
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from threading import Lock
from typing import Annotated, Any

from fastapi import FastAPI, File, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from engine.api.schemas import (
    LoadResponse,
    QueryErrorResponse,
    QueryRequest,
    QueryResponse,
    TableInfo,
)
from engine.api.service import describe_catalog, to_error, to_load_response, to_response
from engine.executor import QueryProcessor
from engine.executor.bulk_load import CsvLoadError, detect_encoding
from engine.executor.native import load_native
from engine.parser.errors import SQLError
from engine.transactions import TransactionError

ORIGENES = ("http://localhost:5173", "http://127.0.0.1:5173")
"""El servidor de desarrollo del frontend. Sin CORS la peticion muere en el
navegador antes de llegar a FastAPI."""


class Motor:
    """Un unico ``QueryProcessor`` por proceso, con el acceso serializado.

    FastAPI corre los endpoints sincronos en un pool de hilos, asi que dos
    peticiones concurrentes tocarian el mismo procesador a la vez. Se serializa
    con un lock porque el procesador guarda la transaccion activa y sus locks:
    manteniendo uno solo, ``BEGIN TRANSACTION`` sobrevive entre peticiones y
    2.1.4 se puede demostrar desde la interfaz. La concurrencia real del motor
    se ensena con la simulacion de hilos del #32, no por HTTP.
    """

    def __init__(
        self,
        catalog: Path | None = None,
        processor: QueryProcessor | None = None,
        database: Any = None,
        native: Any = None,
    ) -> None:
        self._catalog = catalog
        self._processor = processor
        self._database = database
        self._native = native
        self._lock = Lock()

    @contextmanager
    def en_uso(self) -> Iterator[tuple[QueryProcessor, Any, Any]]:
        with self._lock:
            if self._processor is None:
                self._native = load_native()
                ruta = self._catalog or Path(
                    os.environ.get("QUIPUDB_CATALOG", "datos/catalogo.txt")
                )
                ruta.parent.mkdir(parents=True, exist_ok=True)
                self._database = self._native.Database(ruta)
                self._processor = QueryProcessor(self._database)
            yield self._processor, self._database, self._native


def _sin_ubicacion(error: Exception) -> JSONResponse:
    cuerpo = QueryErrorResponse(error=str(error) or type(error).__name__)
    return JSONResponse(status_code=400, content=cuerpo.model_dump())


def create_app(
    catalog: Path | None = None,
    processor: QueryProcessor | None = None,
    database: Any = None,
    native: Any = None,
) -> FastAPI:
    """Construye la aplicacion.

    Los argumentos existen para las pruebas: inyectando procesador, base y
    modulo nativo no hace falta variable de entorno ni catalogo en disco.
    """

    app = FastAPI(
        title="QuipuDB",
        description="Motor de base de datos multimodal escrito desde cero (UTEC, BD2 2026-2)",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(ORIGENES),
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )
    motor = Motor(catalog=catalog, processor=processor, database=database, native=native)
    app.state.motor = motor

    @app.exception_handler(SQLError)
    async def _sql_error(_: Request, error: SQLError) -> JSONResponse:
        # El mensaje viaja sin reformular: el frontend ya muestra estos textos
        # con datos falsos y no deben cambiar al conectarse al motor real.
        return JSONResponse(status_code=400, content=to_error(error).model_dump())

    @app.get("/tables", response_model=list[TableInfo])
    def listar_tablas() -> list[TableInfo]:
        with motor.en_uso() as (_, catalogo, modulo):
            return describe_catalog(catalogo, modulo)

    @app.post("/query", response_model=QueryResponse)
    def ejecutar(peticion: QueryRequest) -> QueryResponse:
        with motor.en_uso() as (processor_, _, _native):
            return to_response(processor_.execute(peticion.sql))

    @app.post(
        "/tables/{tabla}/load",
        response_model=LoadResponse,
        responses={400: {"model": QueryErrorResponse}, 404: {"model": QueryErrorResponse}},
    )
    def cargar_csv(
        tabla: str,
        file: Annotated[UploadFile, File(description="CSV con cabecera, en UTF-8 o cp1252")],
        atomic: Annotated[bool, Query(description="Si una fila falla, no queda ninguna")] = False,
    ) -> LoadResponse | JSONResponse:
        # Starlette ya dejo el archivo en un SpooledTemporaryFile, que pasa a
        # disco despues de 1 MB. Envolverlo en texto sin leerlo entero es lo
        # que permite cargar 100 000 filas sin tenerlas en memoria.
        codificacion = detect_encoding(file.file)
        texto = io.TextIOWrapper(
            file.file,
            encoding="utf-8-sig" if codificacion == "utf-8" else codificacion,
            newline="",
        )
        try:
            with motor.en_uso() as (processor_, catalogo, _native):
                if not catalogo.has_table(tabla):
                    cuerpo = QueryErrorResponse(error=f"la tabla {tabla!r} no existe")
                    return JSONResponse(status_code=404, content=cuerpo.model_dump())
                reporte = processor_.load_csv(tabla, texto, atomic=atomic)
        finally:
            # Sin detach, cerrar el envoltorio cerraria el archivo de Starlette.
            texto.detach()
        return to_load_response(reporte, codificacion)

    @app.exception_handler(CsvLoadError)
    async def _csv_error(_: Request, error: CsvLoadError) -> JSONResponse:
        # Cabecera que no calza, archivo vacio o carga atomica deshecha. La
        # linea es la del CSV, no la de un SQL: el frontend no la subraya.
        cuerpo = QueryErrorResponse(error=error.message, line=error.line)
        return JSONResponse(status_code=400, content=cuerpo.model_dump())

    @app.exception_handler(TransactionError)
    async def _transaction_error(_: Request, error: TransactionError) -> JSONResponse:
        # BEGIN sin END, END sin BEGIN o un lock que no llego a tiempo. Tiene
        # handler propio y no cae en el de `Exception` porque Starlette corre
        # ese ultimo FUERA del middleware de CORS: la respuesta salia sin
        # Access-Control-Allow-Origin y el navegador la descartaba, asi que el
        # frontend mostraba "No se pudo contactar al motor" en vez del error.
        return _sin_ubicacion(error)

    @app.exception_handler(Exception)
    async def _otro_error(request: Request, error: Exception) -> JSONResponse:
        # Un fallo sin ubicacion -- de E/S, por ejemplo -- manda los cuatro
        # campos de posicion en null y el frontend lo muestra sin subrayar.
        # Este handler corre fuera del middleware de CORS (ver arriba), asi
        # que la cabecera se pone a mano.
        respuesta = _sin_ubicacion(error)
        origen = request.headers.get("origin")
        if origen in ORIGENES:
            respuesta.headers["Access-Control-Allow-Origin"] = origen
            respuesta.headers["Vary"] = "Origin"
        return respuesta

    return app


app = create_app()

__all__ = ["app", "create_app"]
