"""Servidor independiente de Rikuq: uvicorn app.main:app --port 8002."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.del_entorno()
    application = FastAPI(title="Rikuq", version="0.1.0")
    application.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.web_origins),
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

    @application.get("/health")
    def health() -> dict[str, str]:
        # Liveness del servidor; no afirma que el motor o los modelos esten listos.
        return {"status": "ok", "app": "rikuq"}

    return application


app = create_app()
