"""Identificacion, foto de referencia y registro de fotos (#342).

Las imagenes de consulta no se guardan; solo ``registrar_foto`` escribe una
imagen en disco y en la base, y solo cuando el usuario lo pide.
"""

import math
from pathlib import Path
from threading import Lock

import httpx
from fastapi import HTTPException

from app import registro
from app.config import Settings
from app.consulta.identificar import UMBRAL_POR_DEFECTO, Identificacion, identificar
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
        # Registrar y eliminar leen el ultimo id y escriben despues: dos a la
        # vez sacarian el mismo id. Espera en vez de responder 429.
        self.escritura = Lock()

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
                            "coincidencias": coincidencias(respuesta),
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

    def registrar_foto(self, nombre: str, datos: bytes, raiz: Path) -> dict:
        nombre = registro.normalizar_nombre(nombre)
        registro.extension_de(datos)  # rechazar antes de cargar los modelos
        # Mismo candado que reconocer: YuNet y ArcFace no se comparten entre hilos.
        if not self.lock.acquire(blocking=False):
            raise HTTPException(429, "hay otra imagen en proceso; vuelve a intentar")
        try:
            vector = registro.vector_unico(self.pipeline.extraer(datos))
        finally:
            self.lock.release()
        with self.escritura, ClienteMotor.desde(self.settings, self.transport) as cliente:
            return registro.registrar(cliente, nombre, datos, vector, raiz)

    def subidos(self) -> list[dict]:
        with ClienteMotor.desde(self.settings, self.transport) as cliente:
            return registro.subidos(cliente)

    def eliminar(self, rostro_id: int, raiz: Path) -> dict:
        with self.escritura, ClienteMotor.desde(self.settings, self.transport) as cliente:
            return registro.eliminar(cliente, rostro_id, raiz)

    def foto_rostro(self, rostro_id: int, raiz: Path) -> Path:
        with ClienteMotor.desde(self.settings, self.transport) as cliente:
            filas = cliente.query(f"SELECT ruta FROM rostros WHERE id = {int(rostro_id)}").dicts()
        if not filas:
            raise HTTPException(404, "esa foto no esta en la base")
        return _archivo_seguro(str(filas[0]["ruta"]), raiz)

    def foto(self, persona_id: int, raiz: Path) -> Path:
        with ClienteMotor.desde(self.settings, self.transport) as cliente:
            filas = cliente.query(
                f"SELECT ruta FROM rostros WHERE persona_id = {int(persona_id)} LIMIT 1"
            ).dicts()
        if not filas:
            raise HTTPException(404, "no hay una foto de referencia para esta persona")
        return _archivo_seguro(str(filas[0]["ruta"]), raiz)


def coincidencias(respuesta: Identificacion) -> list[dict]:
    """Las fotos de la persona identificada que tambien pasan el umbral (#344).

    ``identificar`` ya trae los ``VECINOS`` rostros mas cercanos; sin esto solo
    se veia el mejor, y una persona con dos fotos registradas parecia tener
    una. Salen de la mas parecida a la menos. Un desconocido no expone
    ninguna, igual que no expone la foto de referencia.
    """

    if not respuesta.conocido:
        return []
    persona_id = respuesta.candidatos[0].persona_id
    return [
        {
            "rostro_id": candidato.rostro_id,
            "foto": f"/rostros/{candidato.rostro_id}/foto",
            "distancia": candidato.distancia,
        }
        for candidato in respuesta.candidatos
        if candidato.persona_id == persona_id and candidato.distancia <= UMBRAL_POR_DEFECTO
    ]


def _archivo_seguro(texto: str, raiz: Path) -> Path:
    """El archivo de una ruta de la base, solo si es una imagen dentro de ``raiz``."""

    ruta = Path(texto)
    raiz = raiz.resolve()
    archivo = (raiz / ruta).resolve()
    if ruta.is_absolute() or not archivo.is_relative_to(raiz):
        raise HTTPException(404, "foto de referencia fuera del dataset")
    if archivo.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
        raise HTTPException(404, "formato de foto no permitido")
    if not archivo.is_file():
        raise HTTPException(404, "la foto de referencia no esta en esta maquina")
    return archivo
