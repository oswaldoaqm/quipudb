"""Recuperacion: los chunks que responden una pregunta, con su paper (#245, #252).

Tres modos, todos en QuipuDB:

- ``texto``: ``MATCH`` sobre ``chunks`` con BM25 o TF-IDF. El indice invertido
  de la Parte 3 encuentra los chunks que comparten palabras con la consulta,
  que por eso llega reescrita en ingles.
- ``vectores``: ``SIMILAR_TO`` con el embedding de la pregunta sobre
  ``chunk_vectores``. El HNSW de la Parte 4 encuentra los chunks parecidos en
  significado, en cualquier idioma (#251).
- ``hibrido``: las dos listas fusionadas con Reciprocal Rank Fusion. Un chunk
  que esta arriba en las dos sube; uno que solo esta en una tambien entra.

Despues, el texto de los chunks que solo encontro el HNSW sale de
``chunks WHERE id = n`` (hash sobre ``chunks.id``, #255), y los metadatos de
cada paper de ``papers WHERE id = n`` (hash sobre ``papers.id``). ``MATCH`` y
``SIMILAR_TO`` no admiten ``JOIN`` (#168, #188). Cada SQL se devuelve con su
plan, para que la interfaz muestre como respondio el motor (#249).
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any, Literal

from app.corpus.carga import CORPUS
from app.corpus.esquema import Tablas
from app.motor import ClienteMotor, literal, literal_vector

Ranking = Literal["BM25", "TF_IDF"]
RANKINGS: tuple[Ranking, ...] = ("BM25", "TF_IDF")
Modo = Literal["texto", "vectores", "hibrido"]
MODOS: tuple[Modo, ...] = ("texto", "vectores", "hibrido")
Origen = Literal["texto", "vectores"]

K = 8
MAX_POR_PAPER = 3
RRF_K = 60
"""La constante de Reciprocal Rank Fusion (Cormack et al., 2009): cada lista
aporta ``1 / (RRF_K + posicion)``. Con 60, estar primero en una lista vale
poco mas que estar decimo: ninguna de las dos domina sola."""


@dataclass(frozen=True, slots=True)
class Fragmento:
    chunk_id: int
    paper_id: int
    orden: int
    pagina: int
    texto: str
    relevancia: float
    """El puntaje del modo: BM25 o TF-IDF, el coseno del HNSW o el de RRF."""
    origen: tuple[Origen, ...] = ("texto",)
    """Que busqueda lo encontro: en el modo hibrido pueden ser las dos."""


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
    modo: Modo = "texto"


def sql_match(consulta: str, ranking: Ranking, limite: int, tablas: Tablas = CORPUS) -> str:
    if ranking not in RANKINGS:
        raise ValueError(f"ranking desconocido: {ranking!r}; usa BM25 o TF_IDF")
    return (
        f"SELECT id, paper_id, orden, pagina, texto, SCORE() AS relevancia FROM {tablas.chunks} "
        f"WHERE MATCH(texto, {literal(consulta)}) USING {ranking} LIMIT {int(limite)}"
    )


def sql_similar(vector: Sequence[float], limite: int, tablas: Tablas = CORPUS) -> str:
    return (
        f"SELECT chunk_id, paper_id, SIMILARITY_SCORE() AS similitud FROM {tablas.vectores} "
        f"WHERE SIMILAR_TO(embedding, {literal_vector(vector)}, k={int(limite)}) "
        "USING HNSW WITH METRIC=cosine"
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


def fusionar(listas: Sequence[Sequence[int]], k: int = RRF_K) -> list[tuple[int, float]]:
    """Reciprocal Rank Fusion: ``(chunk_id, puntaje)`` de mayor a menor.

    A igual puntaje gana el que estuvo mas arriba en alguna lista, y despues el
    id menor: el resultado no depende del orden de los diccionarios.
    """

    puntajes: dict[int, float] = {}
    mejor: dict[int, int] = {}
    for lista in listas:
        for posicion, chunk_id in enumerate(lista, start=1):
            puntajes[chunk_id] = puntajes.get(chunk_id, 0.0) + 1 / (k + posicion)
            mejor[chunk_id] = min(mejor.get(chunk_id, posicion), posicion)
    return sorted(puntajes.items(), key=lambda par: (-par[1], mejor[par[0]], par[0]))


def recuperar(
    motor: ClienteMotor,
    consulta: str,
    *,
    k: int = K,
    ranking: Ranking = "BM25",
    max_por_paper: int = MAX_POR_PAPER,
    tablas: Tablas = CORPUS,
    modo: Modo = "texto",
    vector: Sequence[float] | None = None,
) -> Recuperacion:
    """Los chunks que mejor responden y el paper de cada uno.

    ``consulta`` alimenta a ``MATCH`` y ``vector`` (el embedding de la
    pregunta) a ``SIMILAR_TO``; cada modo usa lo suyo.
    """

    if k <= 0 or max_por_paper <= 0:
        raise ValueError("k y max_por_paper tienen que ser positivos")
    if modo not in MODOS:
        raise ValueError(f"modo desconocido: {modo!r}")
    if modo != "texto" and vector is None:
        raise ValueError(f"el modo {modo} necesita el embedding de la pregunta")
    inicio = time.perf_counter()
    consultas: list[ConsultaSql] = []

    def ejecutar(proposito: str, sql: str) -> Any:
        t = time.perf_counter()
        resultado = motor.query(sql)
        consultas.append(
            ConsultaSql(
                proposito, sql, resultado.plan, len(resultado.rows), (time.perf_counter() - t) * 1000
            )
        )
        return resultado

    usa_texto = modo != "vectores" and bool(consulta.strip())
    if modo == "texto" and not usa_texto:
        return Recuperacion(consulta, ranking, (), (), 0.0, modo)

    # Se piden de mas: el tope por paper puede descartar algunos y aun asi
    # tienen que quedar k.
    candidatos = k * max_por_paper
    por_texto: list[Fragmento] = []
    if usa_texto:
        filas = ejecutar(
            f"chunks por palabras ({ranking}, indice invertido)",
            sql_match(consulta, ranking, candidatos, tablas),
        ).rows
        por_texto = [
            Fragmento(int(i), int(p), int(o), int(pg), str(t), float(r), ("texto",))
            for i, p, o, pg, t, r in filas
        ]
    por_vector: list[Fragmento] = []
    if modo != "texto":
        filas = ejecutar(
            "chunks por significado (HNSW, coseno)",
            sql_similar(vector, candidatos, tablas),  # type: ignore[arg-type]
        ).rows
        # El texto todavia no se conoce: se trae solo para los elegidos.
        por_vector = [
            Fragmento(int(i), int(p), -1, -1, "", float(s), ("vectores",)) for i, p, s in filas
        ]

    if modo == "texto":
        ordenados = por_texto
    elif modo == "vectores":
        ordenados = por_vector
    else:
        conocidos = {f.chunk_id: f for f in por_vector} | {f.chunk_id: f for f in por_texto}
        en_texto = {f.chunk_id for f in por_texto}
        en_vector = {f.chunk_id for f in por_vector}
        ordenados = [
            replace(
                conocidos[chunk_id],
                relevancia=puntaje,
                origen=tuple(
                    o for o, ids in (("texto", en_texto), ("vectores", en_vector)) if chunk_id in ids
                ),
            )
            for chunk_id, puntaje in fusionar(
                [[f.chunk_id for f in por_texto], [f.chunk_id for f in por_vector]]
            )
        ]
    fragmentos = seleccionar(ordenados, k, max_por_paper)

    completos = []
    for f in fragmentos:
        if f.texto:
            completos.append(f)
            continue
        filas = ejecutar(
            f"texto del chunk {f.chunk_id} (hash sobre id)",
            f"SELECT orden, pagina, texto FROM {tablas.chunks} WHERE id = {f.chunk_id}",
        ).rows
        if filas:
            orden, pagina, texto = filas[0]
            completos.append(replace(f, orden=int(orden), pagina=int(pagina), texto=str(texto)))

    papers: dict[int, PaperRef] = {}
    for paper_id in dict.fromkeys(f.paper_id for f in completos):
        filas = ejecutar(
            f"paper {paper_id} (hash sobre id)",
            f"SELECT id, arxiv_id, titulo, autores, anio, url FROM {tablas.papers} "
            f"WHERE id = {paper_id}",
        ).rows
        if filas:
            i, arxiv_id, titulo, autores, anio, url = filas[0]
            papers[paper_id] = PaperRef(
                int(i), str(arxiv_id), str(titulo), str(autores), int(anio), str(url)
            )

    evidencias = tuple(
        Evidencia(fragmento, papers[fragmento.paper_id])
        for fragmento in completos
        if fragmento.paper_id in papers
    )
    return Recuperacion(
        consulta,
        ranking,
        evidencias,
        tuple(consultas),
        (time.perf_counter() - inicio) * 1000,
        modo,
    )
