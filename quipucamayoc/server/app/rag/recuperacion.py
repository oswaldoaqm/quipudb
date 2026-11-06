"""Recuperacion: los chunks que responden una pregunta, con su paper (#245).

Dos pasos en QuipuDB, porque ``MATCH`` no admite ``JOIN`` (#168):

1. ``MATCH`` sobre ``chunks`` con BM25 o TF-IDF: el indice invertido de la
   Parte 3 devuelve los chunks por relevancia.
2. Una consulta ``WHERE id = n`` por cada paper distinto: va por el hash sobre
   ``papers.id`` (ADR 0010).

La consulta llega ya en el idioma del corpus: si hay que reescribirla, eso lo
hace la generacion (#246) antes de llamar aqui. Cada SQL ejecutado se devuelve
con su plan, para que la interfaz muestre como respondio el motor (#249).
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from app.corpus.carga import CORPUS
from app.corpus.esquema import Tablas
from app.motor import ClienteMotor, literal

Ranking = Literal["BM25", "TF_IDF"]
RANKINGS: tuple[Ranking, ...] = ("BM25", "TF_IDF")

K = 8
MAX_POR_PAPER = 3


@dataclass(frozen=True, slots=True)
class Fragmento:
    chunk_id: int
    paper_id: int
    orden: int
    pagina: int
    texto: str
    relevancia: float


@dataclass(frozen=True, slots=True)
class PaperRef:
    id: int
    arxiv_id: str
    titulo: str
    autores: str
    anio: int
    url: str


@dataclass(frozen=True, slots=True)
class Evidencia:
    fragmento: Fragmento
    paper: PaperRef


@dataclass(frozen=True, slots=True)
class ConsultaSql:
    """Una sentencia que se ejecuto en el motor, para mostrarla en la interfaz."""

    proposito: str
    sql: str
    plan: dict[str, Any] | None
    filas: int
    tiempo_ms: float


@dataclass(frozen=True, slots=True)
class Recuperacion:
    consulta: str
    ranking: Ranking
    evidencias: tuple[Evidencia, ...]
    consultas: tuple[ConsultaSql, ...]
    tiempo_ms: float


def sql_match(consulta: str, ranking: Ranking, limite: int, tablas: Tablas = CORPUS) -> str:
    if ranking not in RANKINGS:
        raise ValueError(f"ranking desconocido: {ranking!r}; usa BM25 o TF_IDF")
    return (
        f"SELECT id, paper_id, orden, pagina, texto, SCORE() AS relevancia FROM {tablas.chunks} "
        f"WHERE MATCH(texto, {literal(consulta)}) USING {ranking} LIMIT {int(limite)}"
    )


def seleccionar(
    fragmentos: Sequence[Fragmento], k: int, max_por_paper: int
) -> list[Fragmento]:
    """Los ``k`` mas relevantes, con a lo sumo ``max_por_paper`` de cada paper.

    Conserva el orden de relevancia: un paper con muchos chunks parecidos no
    se queda con todo el contexto del LLM.
    """

    elegidos: list[Fragmento] = []
    por_paper: dict[int, int] = {}
    for fragmento in fragmentos:
        if por_paper.get(fragmento.paper_id, 0) >= max_por_paper:
            continue
        por_paper[fragmento.paper_id] = por_paper.get(fragmento.paper_id, 0) + 1
        elegidos.append(fragmento)
        if len(elegidos) == k:
            break
    return elegidos


def recuperar(
    motor: ClienteMotor,
    consulta: str,
    *,
    k: int = K,
    ranking: Ranking = "BM25",
    max_por_paper: int = MAX_POR_PAPER,
    tablas: Tablas = CORPUS,
) -> Recuperacion:
    """Los chunks que mejor responden ``consulta`` y el paper de cada uno."""

    if k <= 0 or max_por_paper <= 0:
        raise ValueError("k y max_por_paper tienen que ser positivos")
    inicio = time.perf_counter()
    consultas: list[ConsultaSql] = []

    def ejecutar(proposito: str, sql: str) -> Any:
        t = time.perf_counter()
        resultado = motor.query(sql)
        consultas.append(
            ConsultaSql(
                proposito,
                sql,
                resultado.plan,
                len(resultado.rows),
                (time.perf_counter() - t) * 1000,
            )
        )
        return resultado

    if not consulta.strip():
        return Recuperacion(consulta, ranking, (), (), 0.0)

    # Se piden de mas: el tope por paper puede descartar algunos y aun asi
    # tienen que quedar k.
    resultado = ejecutar(
        "chunks relevantes (indice invertido)",
        sql_match(consulta, ranking, k * max_por_paper, tablas),
    )
    fragmentos = seleccionar(
        [
            Fragmento(int(i), int(p), int(o), int(pg), str(t), float(r))
            for i, p, o, pg, t, r in resultado.rows
        ],
        k,
        max_por_paper,
    )

    papers: dict[int, PaperRef] = {}
    for paper_id in dict.fromkeys(f.paper_id for f in fragmentos):
        filas = ejecutar(
            f"paper {paper_id} (hash sobre id)",
            f"SELECT id, arxiv_id, titulo, autores, anio, url FROM {tablas.papers} "
            f"WHERE id = {paper_id}",
        ).rows
        if filas:
            i, arxiv_id, titulo, autores, anio, url = filas[0]
            papers[paper_id] = PaperRef(int(i), str(arxiv_id), str(titulo), str(autores), int(anio), str(url))

    evidencias = tuple(
        Evidencia(fragmento, papers[fragmento.paper_id])
        for fragmento in fragmentos
        if fragmento.paper_id in papers
    )
    return Recuperacion(
        consulta,
        ranking,
        evidencias,
        tuple(consultas),
        (time.perf_counter() - inicio) * 1000,
    )
