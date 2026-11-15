"""Identificacion y foto de referencia, sin persistir imagenes de consulta."""

import math
from pathlib import Path
from threading import Lock

import httpx
from fastapi import HTTPException

from app.config import Settings
from app.consulta.identificar import UMBRAL_POR_DEFECTO, identificar
from app.motor import ClienteMotor
from app.pipeline import Pipeline
from app.rostros.errores import EmptyObjectError

DATOS = Path(__file__).resolve().parents[2] / "datos"


class Servicio:
    def __init__(self, settings: Settings, pipeline: Pipeline, transport=None) -> None:
        self.settings = settings
        self.pipeline = pipeline
        self.transport: httpx.BaseTransport | None = transport
        self.lock = Lock()

    def reconocer(self, datos: bytes) -> dict:
        # YuNet y cv2.dnn.Net son mutables. No compartir su forward entre hilos.
        if not self.lock.acquire(blocking=False):
            raise HTTPException(429, "hay otra imagen en proceso; vuelve a intentar")
        try:
            imagen = self.pipeline.extraer(datos)
            if not imagen.rostros:
                raise EmptyObjectError("no hay ningun rostro en la imagen")
            rostros = []
            with ClienteMotor.desde(self.settings, self.transport) as cliente:
                for rostro in imagen.rostros:
                    respuesta = identificar(cliente, rostro.vector, umbral=UMBRAL_POR_DEFECTO)
                    mejor = respuesta.candidatos[0] if respuesta.conocido else None
                    rostros.append(
                        {
                            "persona": respuesta.persona,
                            "persona_id": mejor.persona_id if mejor else None,
                            "foto": f"/personas/{mejor.persona_id}/foto" if mejor else None,
                            "distancia": respuesta.distancia
                            if math.isfinite(respuesta.distancia)
                            else None,
                            "confianza": respuesta.confianza if respuesta.conocido else 0.0,
                            "caja": list(rostro.caja),
                            "fiable": rostro.fiable,
                            "ms_motor": respuesta.ms_motor,
                        }
                    )
            return {
                **rostros[0],
                "rostros": rostros,
                "ancho": imagen.ancho,
                "alto": imagen.alto,
                "umbral": UMBRAL_POR_DEFECTO,
                "ms_motor": sum(r["ms_motor"] for r in rostros),
            }
        finally:
            self.lock.release()

    def foto(self, persona_id: int, raiz: Path) -> Path:
        with ClienteMotor.desde(self.settings, self.transport) as cliente:
            filas = cliente.query(
                f"SELECT ruta FROM rostros WHERE persona_id = {int(persona_id)} LIMIT 1"
            ).dicts()
        if not filas:
            raise HTTPException(404, "no hay una foto de referencia para esta persona")
        ruta = Path(str(filas[0]["ruta"]))
        raiz = raiz.resolve()
        archivo = (raiz / ruta).resolve()
        if ruta.is_absolute() or not archivo.is_relative_to(raiz):
            raise HTTPException(404, "foto de referencia fuera del dataset")
        if archivo.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
            raise HTTPException(404, "formato de foto no permitido")
        if not archivo.is_file():
            raise HTTPException(404, "la foto de referencia no esta en esta maquina")
        return archivo
