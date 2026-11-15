"""Servidor independiente de Rikuq: uvicorn app.main:app --port 8002."""

from pathlib import Path
from time import perf_counter

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from app.config import Settings
from app.entrada import leer_imagen
from app.motor import MotorError, MotorNoDisponible
from app.pipeline import ModeloNoDisponible, Pipeline, PipelineArcFace
from app.rostros.errores import EmptyObjectError, FeatureExtractionError
from app.servicio import DATOS, Servicio


def create_app(
    settings: Settings | None = None,
    *,
    pipeline: Pipeline | None = None,
    engine_transport: httpx.BaseTransport | None = None,
    datos: Path = DATOS,
) -> FastAPI:
    settings = settings or Settings.del_entorno()
    application = FastAPI(title="Rikuq", version="0.1.0")
    servicio = Servicio(settings, pipeline or PipelineArcFace(), engine_transport)
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

    @application.post("/identificar")
    async def identificar_imagen(request: Request):
        inicio = perf_counter()
        imagen = await leer_imagen(request)
        try:
            resultado = await run_in_threadpool(servicio.reconocer, imagen)
        except EmptyObjectError as error:
            return _error(422, "sin_rostro", str(error))
        except FeatureExtractionError as error:
            return _error(422, "imagen_invalida", str(error))
        except ModeloNoDisponible as error:
            return _error(503, "modelo_no_disponible", str(error))
        except MotorNoDisponible:
            return _error(
                503, "motor_no_disponible", "levanta la API de QuipuDB en el puerto configurado"
            )
        except MotorError:
            return _error(
                502, "error_motor", "QuipuDB rechazo la consulta; revisa el esquema de Rikuq"
            )
        resultado["ms_total"] = (perf_counter() - inicio) * 1000
        return JSONResponse(resultado, headers={"Cache-Control": "no-store"})

    @application.get("/personas/{persona_id}/foto")
    def foto_persona(persona_id: int):
        try:
            archivo = servicio.foto(persona_id, datos)
        except MotorNoDisponible:
            return _error(503, "motor_no_disponible", "el motor no esta disponible")
        except MotorError:
            return _error(502, "error_motor", "no se pudo consultar la foto de referencia")
        return FileResponse(
            archivo, headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
        )

    return application


def _error(status: int, codigo: str, mensaje: str) -> JSONResponse:
    return JSONResponse(
        {"codigo": codigo, "error": mensaje},
        status_code=status,
        headers={"Cache-Control": "no-store"},
    )


app = create_app()
