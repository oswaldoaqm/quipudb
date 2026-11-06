"""API HTTP de Quipucamayoc.

Arranque, desde ``quipucamayoc/server``::

    uvicorn app.main:app --reload --port 8001

Necesita el API del motor levantado (``uvicorn engine.api.main:app --port
8000`` desde la raiz del repo) y la configuracion de ``quipucamayoc/.env``.
"""

from __future__ import annotations

import time

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.config import Settings, load_settings
from app.contrato import AskError, AskRequest, AskResponse, a_respuesta
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

    @app.post(
        "/ask",
        response_model=AskResponse,
        responses={502: {"model": AskError}, 503: {"model": AskError}},
    )
    def ask(peticion: AskRequest) -> AskResponse | JSONResponse:
        inicio = time.perf_counter()
        try:
            if not modelos:
                modelos["respuesta"] = GeminiLLM.desde(settings)
                modelos["reescritura"] = GeminiLLM.para_reescritura(settings)
            respuesta = responder(
                motor,
                modelos["respuesta"],
                peticion.pregunta,
                k=peticion.k,
                ranking=peticion.ranking,
                reescribir_consulta=peticion.reescribir,
                llm_reescritura=modelos["reescritura"],
                tablas=tablas,
            )
        except MotorNoDisponible as error:
            return _error(503, "motor", str(error))
        except MotorError as error:
            return _error(502, "motor", f"el motor rechazo la consulta: {error}")
        except LLMNoConfigurado as error:
            return _error(503, "llm", str(error))
        except LLMError as error:
            return _error(502, "llm", str(error))
        return a_respuesta(respuesta, (time.perf_counter() - inicio) * 1000)

    return app


app = create_app()
