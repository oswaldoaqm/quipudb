"""Configuracion del servidor, leida de quipucamayoc/.env y del entorno.

Las variables del entorno ganan sobre el archivo: asi el CI y las pruebas
pueden fijarlas sin tocar el .env de nadie.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values

ENV_FILE = Path(__file__).resolve().parents[2] / ".env"
"""``quipucamayoc/.env``: git lo ignora, ahi va la clave de Gemini."""

DEFAULT_MODEL = "gemini-3.5-flash"
"""Medido el 2026-11-06: gemini-3.8-flash y 3.7-flash respondian 503 por alta
demanda (hasta 87 s antes de rendirse) y 3.5-flash respondia en 5 a 11 s.
Google ya no da gemini-2.5-flash a cuentas nuevas."""
DEFAULT_REWRITE_MODEL = "gemini-3.5-flash-lite"
DEFAULT_EMBEDDING_MODEL = "gemini-embedding-001"
"""Para la busqueda vectorial (#252). gemini-embedding-2 junta una lista de
textos en un solo vector, asi que no sirve para embeber chunks por lotes."""
"""Reescribir la pregunta como palabras clave es facil: un modelo lite tarda
menos de un segundo, contra 6 a 9 s de uno que razona antes de responder."""
DEFAULT_FALLBACK_MODELS = ("gemini-3.5-flash-lite",)
"""Si el modelo principal esta saturado (503) o sin cuota (429), se prueban
estos en orden: los mas nuevos son tambien los mas demandados."""


@dataclass(frozen=True, slots=True)
class Settings:
    # 127.0.0.1 y no localhost: en Windows, localhost prueba primero IPv6 (::1),
    # uvicorn escucha solo en IPv4 y cada peticion pierde ~0.8 s esperando.
    quipudb_api_url: str = "http://127.0.0.1:8000"
    quipudb_timeout_s: float = 30.0
    gemini_model: str = DEFAULT_MODEL
    gemini_fallback_models: tuple[str, ...] = DEFAULT_FALLBACK_MODELS
    gemini_rewrite_model: str = DEFAULT_REWRITE_MODEL
    gemini_embedding_model: str = DEFAULT_EMBEDDING_MODEL
    cors_origins: tuple[str, ...] = ("http://localhost:5174", "http://127.0.0.1:5174")
    # repr=False: la clave no aparece si alguien imprime o registra la
    # configuracion.
    gemini_api_key: str | None = field(default=None, repr=False)

    @property
    def llm_configured(self) -> bool:
        return bool(self.gemini_api_key)


def load_settings(
    env_file: Path | None = ENV_FILE,
    environ: Mapping[str, str] | None = None,
) -> Settings:
    """Arma la configuracion: primero el archivo, encima el entorno."""

    valores: dict[str, str | None] = {}
    if env_file is not None and env_file.is_file():
        valores.update(dotenv_values(env_file))
    valores.update(os.environ if environ is None else environ)

    def texto(nombre: str) -> str | None:
        valor = valores.get(nombre)
        return valor.strip() or None if valor is not None else None

    defaults = Settings()
    timeout = texto("QUIPUDB_TIMEOUT_S")
    origenes = texto("QUIPUCAMAYOC_CORS_ORIGINS")
    respaldos = valores.get("GEMINI_FALLBACK_MODELS")
    return Settings(
        quipudb_api_url=(texto("QUIPUDB_API_URL") or defaults.quipudb_api_url).rstrip("/"),
        quipudb_timeout_s=float(timeout) if timeout else defaults.quipudb_timeout_s,
        gemini_model=texto("GEMINI_MODEL") or defaults.gemini_model,
        gemini_rewrite_model=texto("GEMINI_REWRITE_MODEL") or defaults.gemini_rewrite_model,
        gemini_embedding_model=(
            texto("GEMINI_EMBEDDING_MODEL") or defaults.gemini_embedding_model
        ),
        # Vacia a proposito (GEMINI_FALLBACK_MODELS=) quiere decir sin respaldo.
        gemini_fallback_models=(
            _lista(respaldos) if respaldos is not None else defaults.gemini_fallback_models
        ),
        cors_origins=_lista(origenes) if origenes else defaults.cors_origins,
        gemini_api_key=texto("GEMINI_API_KEY"),
    )


def _lista(valor: str) -> tuple[str, ...]:
    return tuple(parte.strip() for parte in valor.split(",") if parte.strip())
