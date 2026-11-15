"""Configuracion del servidor de Rikuq, leida del entorno (#302).

Lo unico que Rikuq necesita saber del motor es donde escucharlo. Nada mas:
no importa `engine` ni el modulo nativo, solo habla con su API REST.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Self

# 127.0.0.1 y no localhost: en Windows, localhost prueba primero IPv6 (::1),
# uvicorn escucha solo en IPv4 y cada peticion pierde ~0.8 s esperando. Es el
# mismo motivo por el que Quipucamayoc usa la IP.
URL_POR_DEFECTO = "http://127.0.0.1:8000"

# Cargar 4324 rostros son miles de INSERT; el motor puede tardar en responder
# mientras reconstruye un indice.
TIMEOUT_POR_DEFECTO = 60.0


@dataclass(frozen=True, slots=True)
class Settings:
    quipudb_api_url: str = URL_POR_DEFECTO
    quipudb_timeout_s: float = TIMEOUT_POR_DEFECTO
    web_origins: tuple[str, ...] = ("http://127.0.0.1:5175", "http://localhost:5175")

    @classmethod
    def del_entorno(cls, entorno: dict[str, str] | None = None) -> Self:
        """Lee ``QUIPUDB_API_URL`` y ``QUIPUDB_TIMEOUT_S``.

        Una variable vacia vale como ausente: en un ``.env`` a medio llenar,
        ``QUIPUDB_API_URL=`` no deberia dejar al cliente sin destino.
        """

        fuente = os.environ if entorno is None else entorno
        url = (
            (fuente.get("QUIPUDB_URL") or "").strip()
            or (fuente.get("QUIPUDB_API_URL") or "").strip()
            or URL_POR_DEFECTO
        )
        crudo = (fuente.get("QUIPUDB_TIMEOUT_S") or "").strip()
        try:
            timeout = float(crudo) if crudo else TIMEOUT_POR_DEFECTO
        except ValueError:
            timeout = TIMEOUT_POR_DEFECTO
        if timeout <= 0:
            timeout = TIMEOUT_POR_DEFECTO
        origenes = fuente.get("RIKUQ_WEB_ORIGINS", "")
        origins = tuple(o.strip() for o in origenes.split(",") if o.strip())
        return cls(
            quipudb_api_url=url.rstrip("/"),
            quipudb_timeout_s=timeout,
            web_origins=origins or cls().web_origins,
        )


__all__ = ["TIMEOUT_POR_DEFECTO", "URL_POR_DEFECTO", "Settings"]
