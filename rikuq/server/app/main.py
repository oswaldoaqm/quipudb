"""Servidor independiente de Rikuq: uvicorn app.main:app --port 8002."""

from collections.abc import Callable
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
from app.registro import ErrorRegistro
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
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Content-Type"],
    )

    @application.get("/health")
    def health() -> dict[str, str]:
        # Liveness del servidor; no afirma que el motor o los modelos esten listos.
        return {"status": "ok", "app": "rikuq"}

    @application.post("/identificar")
    async def identificar_imagen(request: Request):
        # Las dos mitades se cronometran por separado a proposito. `ms_total`
        # incluia la subida de la imagen, que no es trabajo del servidor: con
        # la camara en vivo, el navegador tarda en codificar cada cuadro y ese
        # rato se contaba como tiempo de reconocimiento. Medido en una maquina
        # del equipo con el procesador limitado por bateria, el mismo servidor
        # respondia en 200 ms a un archivo ya en disco y declaraba 4 954 ms a
        # un cuadro de camara: la diferencia era toda recepcion.
        inicio = perf_counter()
        imagen = await leer_imagen(request)
        recibida = perf_counter()
        resultado = await _ejecutar(servicio.reconocer, imagen)
        if isinstance(resultado, JSONResponse):
            return resultado
        fin = perf_counter()
        resultado["ms_recepcion"] = (recibida - inicio) * 1000
        resultado["ms_proceso"] = (fin - recibida) * 1000
        resultado["ms_total"] = (fin - inicio) * 1000
        return JSONResponse(resultado, headers={"Cache-Control": "no-store"})

    @application.post("/rostros")
    async def registrar_rostro(request: Request, nombre: str = "", consentimiento: bool = False):
        # El permiso se pide en la interfaz, pero quien guarda la foto es este
        # servidor: sin el no se escribe nada, venga de donde venga la peticion.
        if not consentimiento:
            return _error(
                422,
                "consentimiento_requerido",
                "confirma que tienes permiso de la persona para registrar su foto",
            )
        imagen = await leer_imagen(request)
        resultado = await _ejecutar(servicio.registrar_foto, nombre, imagen, datos)
        if isinstance(resultado, JSONResponse):
            return resultado
        return JSONResponse(resultado, status_code=201, headers={"Cache-Control": "no-store"})

    @application.get("/rostros/subidos")
    async def rostros_subidos():
        resultado = await _ejecutar(servicio.subidos)
        if isinstance(resultado, JSONResponse):
            return resultado
        return JSONResponse(resultado, headers={"Cache-Control": "no-store"})

    @application.delete("/rostros/{rostro_id}")
    async def eliminar_rostro(rostro_id: int):
        resultado = await _ejecutar(servicio.eliminar, rostro_id, datos)
        if isinstance(resultado, JSONResponse):
            return resultado
        return JSONResponse(resultado, headers={"Cache-Control": "no-store"})

    @application.get("/rostros/{rostro_id}/foto")
    def foto_rostro(rostro_id: int):
        try:
            archivo = servicio.foto_rostro(rostro_id, datos)
        except MotorNoDisponible:
            return _error(503, "motor_no_disponible", "el motor no esta disponible")
        except MotorError:
            return _error(502, "error_motor", "no se pudo consultar la foto")
        return FileResponse(
            archivo, headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
        )

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


async def _ejecutar(funcion: Callable, *argumentos):
    """Corre ``funcion`` fuera del bucle y traduce sus errores a respuestas."""

    try:
        return await run_in_threadpool(funcion, *argumentos)
    except ErrorRegistro as error:
        return _error(error.status, error.codigo, error.mensaje)
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
        return _error(502, "error_motor", "QuipuDB rechazo la consulta; revisa el esquema de Rikuq")


def _error(status: int, codigo: str, mensaje: str) -> JSONResponse:
    return JSONResponse(
        {"codigo": codigo, "error": mensaje},
        status_code=status,
        headers={"Cache-Control": "no-store"},
    )


app = create_app()
