"""Contrato HTTP de ``POST /ask`` (#247).

Es lo que acuerdan el servidor y la interfaz: ``quipucamayoc/web/src/api/types.ts``
lo repite en TypeScript. Cambiar un campo aqui es cambiarlo alla.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.rag.generacion import Respuesta
from app.rag.recuperacion import RANKINGS, Evidencia, Ranking


class AskRequest(BaseModel):
    pregunta: str = Field(min_length=1, max_length=1000)
    k: int = Field(default=8, ge=1, le=20, description="chunks que se le envian al LLM")
    ranking: Ranking = Field(default="BM25", description=f"uno de {', '.join(RANKINGS)}")
    reescribir: bool = Field(
        default=True, description="reescribir la pregunta como palabras clave en ingles"
    )


class PaperOut(BaseModel):
    id: int
    arxiv_id: str
    titulo: str
    autores: str
    anio: int
    url: str


class FuenteOut(BaseModel):
    """Un chunk que se le envio al LLM. ``numero`` es el de las citas ``[n]``."""

    numero: int
    chunk_id: int
    orden: int
    pagina: int
    texto: str
    relevancia: float
    citada: bool
    paper: PaperOut


class SqlOut(BaseModel):
    """Una sentencia que se ejecuto en QuipuDB, con el plan que devolvio el motor."""

    proposito: str
    sql: str
    plan: dict[str, Any] | None
    filas: int
    tiempo_ms: float


class TiemposOut(BaseModel):
    reescritura_ms: float
    recuperacion_ms: float
    generacion_ms: float
    total_ms: float


class AskResponse(BaseModel):
    pregunta: str
    consulta: str
    """Lo que se busco con MATCH: la reescritura en ingles o la pregunta."""
    respuesta: str
    """Con citas ``[n]`` que apuntan a ``fuentes[n - 1]``."""
    modelo: str | None
    """El modelo de Gemini que respondio; ``None`` si no hizo falta llamarlo."""
    ranking: Ranking
    citas: list[int]
    citas_descartadas: list[int]
    fuentes: list[FuenteOut]
    sql: list[SqlOut]
    tiempos: TiemposOut


class AskError(BaseModel):
    error: str
    origen: Literal["motor", "llm"]
    """Quien fallo: la interfaz los muestra distinto."""


def _paper(e: Evidencia) -> PaperOut:
    p = e.paper
    return PaperOut(
        id=p.id, arxiv_id=p.arxiv_id, titulo=p.titulo, autores=p.autores, anio=p.anio, url=p.url
    )


def a_respuesta(r: Respuesta, total_ms: float) -> AskResponse:
    citadas = {c.numero for c in r.citas}
    return AskResponse(
        pregunta=r.pregunta,
        consulta=r.consulta,
        respuesta=r.texto,
        modelo=r.modelo,
        ranking=r.recuperacion.ranking,
        citas=[c.numero for c in r.citas],
        citas_descartadas=list(r.citas_descartadas),
        fuentes=[
            FuenteOut(
                numero=numero,
                chunk_id=e.fragmento.chunk_id,
                orden=e.fragmento.orden,
                pagina=e.fragmento.pagina,
                texto=e.fragmento.texto,
                relevancia=e.fragmento.relevancia,
                citada=numero in citadas,
                paper=_paper(e),
            )
            for numero, e in enumerate(r.evidencias, start=1)
        ],
        sql=[
            SqlOut(
                proposito=c.proposito, sql=c.sql, plan=c.plan, filas=c.filas, tiempo_ms=c.tiempo_ms
            )
            for c in r.recuperacion.consultas
        ],
        tiempos=TiemposOut(
            reescritura_ms=r.tiempos.reescritura_ms,
            recuperacion_ms=r.tiempos.recuperacion_ms,
            generacion_ms=r.tiempos.generacion_ms,
            total_ms=total_ms,
        ),
    )
