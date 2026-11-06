"""Contrato HTTP de ``POST /ask`` (#247).

Es lo que acuerdan el servidor y la interfaz: ``quipucamayoc/web/src/api/types.ts``
lo repite en TypeScript. Cambiar un campo aqui es cambiarlo alla.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.biblioteca import Agregado, Listado
from app.rag.generacion import Respuesta
from app.rag.recuperacion import RANKINGS, ConsultaSql, Evidencia, Ranking, Recuperacion


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


class EventoConsulta(BaseModel):
    etapa: Literal["consulta"] = "consulta"
    consulta: str


class EventoRecuperacion(BaseModel):
    """Las fuentes antes de que Gemini responda: ninguna esta citada todavia."""

    etapa: Literal["recuperacion"] = "recuperacion"
    fuentes: list[FuenteOut]
    sql: list[SqlOut]
    recuperacion_ms: float


class EventoRespuesta(BaseModel):
    etapa: Literal["respuesta"] = "respuesta"
    respuesta: AskResponse


class EventoError(BaseModel):
    etapa: Literal["error"] = "error"
    error: str
    origen: Literal["motor", "llm"]


def fuentes(evidencias: tuple[Evidencia, ...], citadas: set[int] = frozenset()) -> list[FuenteOut]:
    return [
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
        for numero, e in enumerate(evidencias, start=1)
    ]


def sqls(consultas: tuple[ConsultaSql, ...]) -> list[SqlOut]:
    return [
        SqlOut(proposito=c.proposito, sql=c.sql, plan=c.plan, filas=c.filas, tiempo_ms=c.tiempo_ms)
        for c in consultas
    ]


def evento_recuperacion(r: Recuperacion) -> EventoRecuperacion:
    return EventoRecuperacion(
        fuentes=fuentes(r.evidencias), sql=sqls(r.consultas), recuperacion_ms=r.tiempo_ms
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
        fuentes=fuentes(r.evidencias, citadas),
        sql=sqls(r.recuperacion.consultas),
        tiempos=TiemposOut(
            reescritura_ms=r.tiempos.reescritura_ms,
            recuperacion_ms=r.tiempos.recuperacion_ms,
            generacion_ms=r.tiempos.generacion_ms,
            total_ms=total_ms,
        ),
    )


# ---------------------------------------------------------------------------
# Biblioteca (#250)
# ---------------------------------------------------------------------------


class FichaOut(BaseModel):
    id: int
    arxiv_id: str
    titulo: str
    autores: str
    anio: int
    categoria: str
    abstract: str
    url: str
    chunks: int


class PapersResponse(BaseModel):
    papers: list[FichaOut]
    sql: list[SqlOut]


class FallidoOut(BaseModel):
    arxiv_id: str
    motivo: str


class TiemposCargaOut(BaseModel):
    extraccion_s: float
    chunking_s: float
    carga_s: float
    indice_s: float
    """La reconstruccion del indice de texto: la paga la carga, no la siguiente pregunta."""


class AgregadoOut(BaseModel):
    arxiv_id: str
    chunks: int
    tiempos: TiemposCargaOut


class ErrorSimple(BaseModel):
    error: str


def a_papers(listado: Listado) -> PapersResponse:
    return PapersResponse(
        papers=[FichaOut(**{campo: getattr(p, campo) for campo in FichaOut.model_fields}) for p in listado.papers],
        sql=sqls(listado.sql),
    )


def a_agregado(agregado: Agregado) -> AgregadoOut:
    t = agregado.reporte.tiempos
    return AgregadoOut(
        arxiv_id=agregado.arxiv_id,
        chunks=agregado.reporte.chunks,
        tiempos=TiemposCargaOut(
            extraccion_s=t.extraccion_s, chunking_s=t.chunking_s, carga_s=t.carga_s, indice_s=t.indice_s
        ),
    )
