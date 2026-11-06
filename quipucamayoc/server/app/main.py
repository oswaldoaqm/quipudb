"""API HTTP de Quipucamayoc.

Arranque, desde ``quipucamayoc/server``::

    uvicorn app.main:app --reload --port 8001

Necesita el API del motor levantado (``uvicorn engine.api.main:app --port
8000`` desde la raiz del repo) y la configuracion de ``quipucamayoc/.env``.
"""

from __future__ import annotations

import queue
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated

import httpx
from fastapi import FastAPI, File, Form, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from app.biblioteca import agregar, listar
from app.config import Settings, load_settings
from app.contrato import (
    AgregadoOut,
    AskError,
    AskRequest,
    AskResponse,
    ErrorSimple,
    EventoConsulta,
    EventoError,
    EventoRespuesta,
    PapersResponse,
    a_agregado,
    a_papers,
    a_respuesta,
    evento_recuperacion,
)
from app.corpus.arxiv import DATOS
from app.corpus.carga import CORPUS
from app.corpus.esquema import Tablas
from app.motor import ClienteMotor, MotorError, MotorNoDisponible, usa_tls
from app.rag.generacion import responder
from app.rag.llm import LLM, GeminiLLM, LLMError, LLMNoConfigurado


class EngineHealth(BaseModel):
    url: str
    reachable: bool
    tables: int | None = None
    error: str | None = None


class LlmHealth(BaseModel):
    provider: str = "gemini"
    model: str
    configured: bool
    """Si hay ``GEMINI_API_KEY``. La clave nunca viaja en la respuesta."""


class HealthResponse(BaseModel):
    status: str
    engine: EngineHealth
    llm: LlmHealth


def check_engine(settings: Settings, transport: httpx.BaseTransport | None = None) -> EngineHealth:
    """Pregunta al motor por sus tablas: si responde, esta vivo y es QuipuDB."""

    try:
        with httpx.Client(
            base_url=settings.quipudb_api_url,
            timeout=min(settings.quipudb_timeout_s, 5.0),
            transport=transport,
            verify=usa_tls(settings.quipudb_api_url),
        ) as cliente:
            respuesta = cliente.get("/tables")
            respuesta.raise_for_status()
            return EngineHealth(
                url=settings.quipudb_api_url, reachable=True, tables=len(respuesta.json())
            )
    except (httpx.HTTPError, ValueError) as error:
        return EngineHealth(
            url=settings.quipudb_api_url,
            reachable=False,
            error=f"{type(error).__name__}: {error}",
        )


MAX_PDF = 50 * 1024 * 1024


def _error(status: int, origen: str, mensaje: str) -> JSONResponse:
    return JSONResponse(status_code=status, content=AskError(error=mensaje, origen=origen).model_dump())


def create_app(
    settings: Settings | None = None,
    engine_transport: httpx.BaseTransport | None = None,
    *,
    motor: ClienteMotor | None = None,
    llm: LLM | None = None,
    llm_reescritura: LLM | None = None,
    tablas: Tablas = CORPUS,
    carpeta_subidos: Path = DATOS / "subidos",
) -> FastAPI:
    """``motor`` y los LLM se inyectan en las pruebas; si no, salen de ``settings``.

    Los LLM se crean en la primera pregunta: sin clave, el servidor arranca
    igual y ``/ask`` responde 503 diciendo que falta.
    """

    settings = settings or load_settings()
    app = FastAPI(title="Quipucamayoc", version="0.1.0")
    app.state.settings = settings
    motor = motor or ClienteMotor.desde(settings, engine_transport)
    modelos: dict[str, LLM] = {}
    if llm is not None:
        modelos["respuesta"] = llm
        modelos["reescritura"] = llm_reescritura or llm
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        engine = check_engine(settings, engine_transport)
        return HealthResponse(
            status="ok" if engine.reachable else "degraded",
            engine=engine,
            llm=LlmHealth(model=settings.gemini_model, configured=settings.llm_configured),
        )

    def preparar_modelos() -> None:
        if not modelos:
            modelos["respuesta"] = GeminiLLM.desde(settings)
            modelos["reescritura"] = GeminiLLM.para_reescritura(settings)

    def preguntar(peticion: AskRequest, avance=None):
        return responder(
            motor,
            modelos["respuesta"],
            peticion.pregunta,
            k=peticion.k,
            ranking=peticion.ranking,
            reescribir_consulta=peticion.reescribir,
            llm_reescritura=modelos["reescritura"],
            tablas=tablas,
            avance=avance,
        )

    @app.post(
        "/ask",
        response_model=AskResponse,
        responses={502: {"model": AskError}, 503: {"model": AskError}},
    )
    def ask(peticion: AskRequest) -> AskResponse | JSONResponse:
        inicio = time.perf_counter()
        try:
            preparar_modelos()
            respuesta = preguntar(peticion)
        except MotorNoDisponible as error:
            return _error(503, "motor", str(error))
        except MotorError as error:
            return _error(502, "motor", f"el motor rechazo la consulta: {error}")
        except LLMNoConfigurado as error:
            return _error(503, "llm", str(error))
        except LLMError as error:
            return _error(502, "llm", str(error))
        return a_respuesta(respuesta, (time.perf_counter() - inicio) * 1000)

    @app.post(
        "/ask/stream",
        response_model=None,
        responses={
            200: {"content": {"application/x-ndjson": {}}, "description": "una linea JSON por etapa"},
            503: {"model": AskError},
        },
    )
    def ask_stream(peticion: AskRequest) -> StreamingResponse | JSONResponse:
        """Lo mismo que ``/ask``, pero avisando cada etapa en una linea JSON.

        ``consulta`` -> ``recuperacion`` -> ``respuesta``, o ``error`` en
        cualquier momento. La interfaz muestra las fuentes mientras Gemini
        todavia escribe.
        """

        try:
            preparar_modelos()
        except LLMNoConfigurado as error:
            return _error(503, "llm", str(error))

        def etapas() -> Iterator[str]:
            cola: queue.Queue[BaseModel | None] = queue.Queue()
            inicio = time.perf_counter()

            def avance(etapa: str, datos: object) -> None:
                if etapa == "consulta":
                    cola.put(EventoConsulta(consulta=str(datos)))
                elif etapa == "recuperacion":
                    cola.put(evento_recuperacion(datos))  # type: ignore[arg-type]

            def trabajar() -> None:
                try:
                    respuesta = preguntar(peticion, avance)
                    total = (time.perf_counter() - inicio) * 1000
                    cola.put(EventoRespuesta(respuesta=a_respuesta(respuesta, total)))
                except MotorNoDisponible as error:
                    cola.put(EventoError(error=str(error), origen="motor"))
                except MotorError as error:
                    cola.put(EventoError(error=f"el motor rechazo la consulta: {error}", origen="motor"))
                except LLMError as error:
                    cola.put(EventoError(error=str(error), origen="llm"))
                finally:
                    cola.put(None)

            threading.Thread(target=trabajar, daemon=True).start()
            while (evento := cola.get()) is not None:
                yield evento.model_dump_json() + "\n"

        return StreamingResponse(etapas(), media_type="application/x-ndjson")

    @app.get("/papers", response_model=PapersResponse, responses={503: {"model": ErrorSimple}})
    def papers(
        anio_desde: Annotated[int | None, Query(ge=0, le=9999)] = None,
        anio_hasta: Annotated[int | None, Query(ge=0, le=9999)] = None,
        autor: Annotated[str | None, Query(max_length=100)] = None,
    ) -> PapersResponse | JSONResponse:
        try:
            listado = listar(
                motor, tablas, anio_desde=anio_desde, anio_hasta=anio_hasta, autor=autor
            )
        except MotorError as error:
            return JSONResponse(status_code=503, content={"error": str(error)})
        return a_papers(listado)

    @app.post(
        "/papers",
        response_model=AgregadoOut,
        status_code=201,
        responses={400: {"model": ErrorSimple}, 503: {"model": ErrorSimple}},
    )
    def agregar_paper(
        archivo: Annotated[UploadFile, File(description="el PDF del paper")],
        titulo: Annotated[str, Form(min_length=1, max_length=300)],
        autores: Annotated[str, Form(description="separados por comas")],
        anio: Annotated[int, Form(ge=1900, le=2100)],
        abstract: Annotated[str, Form(max_length=2000)] = "",
    ) -> AgregadoOut | JSONResponse:
        contenido = archivo.file.read(MAX_PDF + 1)
        if len(contenido) > MAX_PDF:
            return JSONResponse(status_code=400, content={"error": "el PDF pasa de 50 MB"})
        try:
            agregado = agregar(
                motor,
                tablas,
                carpeta_subidos,
                pdf=contenido,
                titulo=titulo,
                autores=autores.split(","),
                anio=anio,
                abstract=abstract,
            )
        except ValueError as error:
            return JSONResponse(status_code=400, content={"error": str(error)})
        except MotorError as error:
            return JSONResponse(status_code=503, content={"error": str(error)})
        if agregado.reporte.fallidos:
            _, motivo = agregado.reporte.fallidos[0]
            return JSONResponse(status_code=400, content={"error": motivo})
        return a_agregado(agregado)

    return app


app = create_app()
