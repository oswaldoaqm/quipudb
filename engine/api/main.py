"""API HTTP del motor: lo unico que el frontend (2.1.5) consume.

`POST /query` y `GET /tables`, con la forma que fija
`frontend/src/api/types.ts`, y `POST /tables/{tabla}/load` para cargar un CSV.
`POST /tables/{tabla}/load-media` inicia una carga multimedia; su progreso
se consulta en `GET /tables/{tabla}/load-media/{job_id}`. Levantar con:

    uvicorn engine.api.main:app --reload --port 8000

El catalogo sale de la variable de entorno ``QUIPUDB_CATALOG``; por defecto,
``datos/catalogo.txt`` relativo a donde se levante el servidor.
"""

from __future__ import annotations

import io
import json
import os
from collections.abc import Iterator
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from threading import Lock
from typing import Annotated, Any

from fastapi import FastAPI, File, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile as StarletteUploadFile
from starlette.exceptions import HTTPException

from engine.api.media_load import MediaJobs, MediaLoadConflict
from engine.api.schemas import (
    LoadResponse,
    MediaJobResponse,
    MediaLoadRequest,
    QueryErrorResponse,
    QueryRequest,
    QueryResponse,
    TableInfo,
    UploadedFile,
    UploadRejected,
    UploadResponse,
    VocabularyInfo,
    VocabularyJobResponse,
    VocabularyRegisterRequest,
    VocabularyTrainRequest,
)
from engine.api.service import describe_catalog, to_error, to_load_response, to_response
from engine.api.uploads import (
    MAX_FILES_PER_REQUEST,
    UploadError,
    existing_upload_folder,
    new_upload_folder,
    remove_if_empty,
    save_files,
)
from engine.api.vocabularies import (
    VocabularyConflict,
    VocabularyJobs,
    VocabularyRegistry,
    list_vocabularies,
    registry_key,
    vector_column,
)
from engine.executor import QueryProcessor
from engine.executor.bulk_load import CsvLoadError, detect_encoding
from engine.executor.media_load import MediaLoadError, media_path
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
        media_root: Path | None = None,
        registry: VocabularyRegistry | None = None,
    ) -> None:
        self._registry = registry
        self._catalog = catalog
        self._processor = processor
        self._database = database
        self._native = native
        self._media_root = media_root
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
                vocabularies = json.loads(os.environ.get("QUIPUDB_MEDIA_VOCABULARIES", "{}"))
                if not isinstance(vocabularies, dict) or not all(
                    isinstance(key, str) and isinstance(value, str)
                    for key, value in vocabularies.items()
                ):
                    raise ValueError("QUIPUDB_MEDIA_VOCABULARIES debe ser un objeto JSON de rutas")
                if self._registry is not None:
                    # Las asociadas desde la API (#287) se suman a las del entorno.
                    vocabularies = self._registry.add_initial(vocabularies)
                self._processor = QueryProcessor(
                    self._database,
                    media_root=self._media_root or os.environ.get(
                        "QUIPUDB_MEDIA_ROOT", "datos/multimedia",
                    ),
                    media_vocabularies=vocabularies,
                )
            yield self._processor, self._database, self._native

    def flush(self) -> None:
        """Vacia a disco tablas e indices abiertos, si el motor llego a abrirse.

        El grafo de un HNSW vive en memoria y solo baja a disco con
        ``Database.flush()`` o con el destructor del core, y al apagar uvicorn
        los destructores no corren: sin esto, reiniciar la API dejaba cada HNSW
        vacio mientras las tablas, IVF y los indices de texto sobrevivian.
        """

        with self._lock:
            if self._database is not None:
                self._database.flush()


def _sin_ubicacion(error: Exception) -> JSONResponse:
    cuerpo = QueryErrorResponse(error=str(error) or type(error).__name__)
    return JSONResponse(status_code=400, content=cuerpo.model_dump())


def create_app(
    catalog: Path | None = None,
    processor: QueryProcessor | None = None,
    database: Any = None,
    native: Any = None,
    media_root: Path | None = None,
) -> FastAPI:
    """Construye la aplicacion.

    Los argumentos existen para las pruebas: inyectando procesador, base y
    modulo nativo no hace falta variable de entorno ni catalogo en disco.
    """

    root = media_root if media_root is not None else Path(
        os.environ.get("QUIPUDB_MEDIA_ROOT", "datos/multimedia")
    )
    registry = VocabularyRegistry(root)
    if processor is not None:
        for key, path in registry.saved().items():
            processor.register_vocabulary(key, path)
    motor = Motor(
        catalog=catalog,
        processor=processor,
        database=database,
        native=native,
        media_root=root,
        registry=registry,
    )
    media_jobs = MediaJobs(motor, root)
    vocabulary_jobs = VocabularyJobs(motor, root, registry)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        try:
            yield
        finally:
            # Primero las cargas: un trabajo multimedia puede seguir
            # insertando en los indices hasta que termine su lote.
            await run_in_threadpool(media_jobs.close)
            await run_in_threadpool(vocabulary_jobs.close)
            await run_in_threadpool(motor.flush)

    app = FastAPI(
        title="QuipuDB",
        description="Motor de base de datos multimodal escrito desde cero (UTEC, BD2 2026-2)",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(ORIGENES),
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )
    app.state.motor = motor
    app.state.media_jobs = media_jobs

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

    @app.post(
        "/tables/{tabla}/load-media",
        response_model=MediaJobResponse,
        status_code=202,
        responses={
            400: {"model": QueryErrorResponse},
            404: {"model": QueryErrorResponse},
            409: {"model": QueryErrorResponse},
        },
    )
    def cargar_multimedia(tabla: str, peticion: MediaLoadRequest) -> MediaJobResponse | JSONResponse:
        with motor.en_uso() as (_, catalogo, _native):
            if not catalogo.has_table(tabla):
                return JSONResponse(
                    status_code=404,
                    content=QueryErrorResponse(error=f"la tabla {tabla!r} no existe").model_dump(),
                )
        return media_jobs.start(tabla, peticion)

    @app.get("/tables/{tabla}/load-media/{job_id}", response_model=MediaJobResponse)
    def progreso_multimedia(tabla: str, job_id: str) -> MediaJobResponse | JSONResponse:
        try:
            return media_jobs.get(tabla, job_id)
        except KeyError:
            return JSONResponse(
                status_code=404,
                content=QueryErrorResponse(error="el trabajo multimedia no existe").model_dump(),
            )

    @app.post(
        "/vocabularies",
        response_model=VocabularyJobResponse,
        status_code=202,
        responses={
            400: {"model": QueryErrorResponse},
            404: {"model": QueryErrorResponse},
            409: {"model": QueryErrorResponse},
        },
    )
    def entrenar_vocabulario(
        peticion: VocabularyTrainRequest,
    ) -> VocabularyJobResponse | JSONResponse:
        """Entrena un vocabulario con una carpeta del servidor, en segundo plano (#287)."""

        try:
            return vocabulary_jobs.start(peticion)
        except KeyError:
            return JSONResponse(
                status_code=404,
                content=QueryErrorResponse(
                    error=f"la tabla {peticion.table!r} no existe"
                ).model_dump(),
            )

    @app.get("/vocabularies", response_model=list[VocabularyInfo])
    def listar_vocabularios() -> list[VocabularyInfo]:
        return list_vocabularies(root, registry)

    @app.get("/vocabularies/{job_id}", response_model=VocabularyJobResponse)
    def progreso_vocabulario(job_id: str) -> VocabularyJobResponse | JSONResponse:
        try:
            return vocabulary_jobs.get(job_id)
        except KeyError:
            return JSONResponse(
                status_code=404,
                content=QueryErrorResponse(error="el entrenamiento no existe").model_dump(),
            )

    @app.put(
        "/tables/{tabla}/vocabulary",
        response_model=VocabularyInfo,
        responses={400: {"model": QueryErrorResponse}, 404: {"model": QueryErrorResponse}},
    )
    def asociar_vocabulario(
        tabla: str, peticion: VocabularyRegisterRequest
    ) -> VocabularyInfo | JSONResponse:
        """Asocia un vocabulario ya entrenado a la tabla, sin reiniciar la API."""

        from engine.multimedia.features import Vocabulary, VocabularyError

        path = media_path(root.resolve(), peticion.vocabulary, directory=False)
        try:
            vocabulary = Vocabulary.load(path)
        except VocabularyError as error:
            raise MediaLoadError(str(error)) from None
        relative = path.relative_to(root.resolve()).as_posix()
        with motor.en_uso() as (processor_, catalogo, modulo):
            try:
                column, dimension = vector_column(catalogo, modulo, tabla, peticion.column)
            except KeyError:
                return JSONResponse(
                    status_code=404,
                    content=QueryErrorResponse(error=f"la tabla {tabla!r} no existe").model_dump(),
                )
            if dimension != vocabulary.k:
                raise MediaLoadError(
                    f"la columna {column!r} es VECTOR({dimension}) y el vocabulario tiene "
                    f"{vocabulary.k} palabras"
                )
            registry.register(registry_key(tabla, peticion.column), relative, processor_)
        return next(v for v in list_vocabularies(root, registry) if v.path == relative)

    @app.post(
        "/media/uploads",
        response_model=UploadResponse,
        responses={400: {"model": QueryErrorResponse}},
    )
    async def subir_multimedia(
        request: Request,
        max_file_bytes: Annotated[
            int, Query(ge=1, le=64 * 1024**2, description="Límite por archivo")
        ] = 32 * 1024**2,
    ) -> UploadResponse | JSONResponse:
        """Guarda imágenes o audios del navegador dentro de la raíz multimedia (#288).

        Campos multipart: ``files`` (uno o varios; el nombre puede traer
        subcarpetas), ``name`` para la carpeta nueva y ``folder`` para agregar
        a una carpeta de ``subidas/`` devuelta por una petición anterior.
        """

        try:
            form = await request.form(max_files=MAX_FILES_PER_REQUEST, max_fields=8)
        except HTTPException as error:
            mensaje = str(error.detail)
            if "Too many files" in mensaje:
                mensaje = (
                    f"se admiten hasta {MAX_FILES_PER_REQUEST} archivos por petición; "
                    "sube el resto en otra petición con el mismo folder"
                )
            return JSONResponse(
                status_code=400,
                content=QueryErrorResponse(error=f"peticion invalida: {mensaje}").model_dump(),
            )
        uploads = [f for f in form.getlist("files") if isinstance(f, StarletteUploadFile)]
        if not uploads:
            raise UploadError("la petición no trae archivos en el campo files")
        folder_value = form.get("folder")
        name_value = form.get("name")
        resolved_root = root.resolve()
        try:
            if isinstance(folder_value, str) and folder_value:
                folder = existing_upload_folder(resolved_root, folder_value)
                created = False
            else:
                folder = new_upload_folder(
                    resolved_root, name_value if isinstance(name_value, str) else None
                )
                created = True
            report = await run_in_threadpool(
                save_files,
                resolved_root,
                folder,
                [(u.filename or "", u.file) for u in uploads],
                max_file_bytes=max_file_bytes,
            )
            if created and not report.saved:
                remove_if_empty(folder)
        finally:
            await form.close()
        return UploadResponse(
            folder=report.folder,
            saved=len(report.saved),
            files=[UploadedFile(name=n, path=p, bytes=b) for n, p, b in report.saved],
            rejected=[UploadRejected(name=n, error=e) for n, e in report.rejected],
        )

    @app.exception_handler(VocabularyConflict)
    async def _vocabulary_conflict(_: Request, error: VocabularyConflict) -> JSONResponse:
        return JSONResponse(
            status_code=409, content=QueryErrorResponse(error=str(error)).model_dump()
        )

    @app.exception_handler(MediaLoadConflict)
    async def _media_conflict(_: Request, error: MediaLoadConflict) -> JSONResponse:
        return JSONResponse(
            status_code=409, content=QueryErrorResponse(error=str(error)).model_dump()
        )

    @app.exception_handler(MediaLoadError)
    async def _media_error(_: Request, error: MediaLoadError) -> JSONResponse:
        return _sin_ubicacion(error)

    @app.exception_handler(CsvLoadError)
    async def _csv_error(_: Request, error: CsvLoadError) -> JSONResponse:
        # Cabecera que no calza, archivo vacio o carga atomica deshecha. La
        # linea es la del CSV, no la de un SQL: el frontend no la subraya.
        cuerpo = QueryErrorResponse(error=error.message, line=error.line)
        return JSONResponse(status_code=400, content=cuerpo.model_dump())

    @app.exception_handler(RequestValidationError)
    async def _peticion_invalida(_: Request, error: RequestValidationError) -> JSONResponse:
        # Sin esto FastAPI responde {"detail": [...]}, que el frontend no
        # entiende y muestra como "El motor respondio 422". Se mantiene el 422
        # y se usa la misma forma que el resto de errores.
        partes = []
        for detalle in error.errors():
            campo = ".".join(str(parte) for parte in detalle.get("loc", ())[1:])
            mensaje = str(detalle.get("msg", "valor invalido"))
            partes.append(f"{campo}: {mensaje}" if campo else mensaje)
        cuerpo = QueryErrorResponse(error="peticion invalida: " + "; ".join(partes))
        return JSONResponse(status_code=422, content=cuerpo.model_dump())

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
