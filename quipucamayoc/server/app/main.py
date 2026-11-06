"""API HTTP de Quipucamayoc.

Arranque, desde ``quipucamayoc/server``::

    uvicorn app.main:app --reload --port 8001

Necesita el API del motor levantado (``uvicorn engine.api.main:app --port
8000`` desde la raiz del repo) y la configuracion de ``quipucamayoc/.env``.
"""

from __future__ import annotations

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.config import Settings, load_settings
from app.motor import usa_tls


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


def create_app(
    settings: Settings | None = None,
    engine_transport: httpx.BaseTransport | None = None,
) -> FastAPI:
    settings = settings or load_settings()
    app = FastAPI(title="Quipucamayoc", version="0.1.0")
    app.state.settings = settings
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

    return app


app = create_app()
