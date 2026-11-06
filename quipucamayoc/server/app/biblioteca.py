"""La biblioteca: que papers conoce Quipucamayoc y agregar uno nuevo (#250).

Los filtros usan lo que el SQL del motor ofrece. Un ``WHERE`` admite una sola
condicion, sin ``AND`` ni ``LIKE``:

- el rango de anios va al motor como ``WHERE anio BETWEEN a AND b`` y lo
  resuelve el B+ sobre ``papers.anio`` (ADR 0010);
- el autor se filtra en el servidor sobre ese resultado.

Cuantos chunks tiene cada paper sale de un ``GROUP BY`` del motor (external
hashing de la Parte 1).
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from app.corpus.arxiv import Paper
from app.corpus.carga import Reporte, cargar
from app.corpus.esquema import Tablas
from app.motor import ClienteMotor
from app.rag.recuperacion import ConsultaSql

PREFIJO_LOCAL = "local-"
"""Los papers subidos a mano no tienen id de arXiv: llevan uno propio."""


@dataclass(frozen=True, slots=True)
class FichaPaper:
    id: int
    arxiv_id: str
    titulo: str
    autores: str
    anio: int
    categoria: str
    abstract: str
    url: str
    chunks: int


@dataclass(frozen=True, slots=True)
class Listado:
    papers: tuple[FichaPaper, ...]
    sql: tuple[ConsultaSql, ...]


def _ejecutar(motor: ClienteMotor, proposito: str, sql: str, registro: list[ConsultaSql]):
    inicio = time.perf_counter()
    resultado = motor.query(sql)
    registro.append(
        ConsultaSql(
            proposito, sql, resultado.plan, len(resultado.rows), (time.perf_counter() - inicio) * 1000
        )
    )
    return resultado


def listar(
    motor: ClienteMotor,
    tablas: Tablas,
    *,
    anio_desde: int | None = None,
    anio_hasta: int | None = None,
    autor: str | None = None,
) -> Listado:
    registro: list[ConsultaSql] = []
    columnas = "id, arxiv_id, titulo, autores, anio, categoria, abstract, url"
    if anio_desde is None and anio_hasta is None:
        sql = f"SELECT {columnas} FROM {tablas.papers}"
        proposito = "todos los papers"
    else:
        desde = anio_desde if anio_desde is not None else 0
        hasta = anio_hasta if anio_hasta is not None else 9999
        sql = f"SELECT {columnas} FROM {tablas.papers} WHERE anio BETWEEN {int(desde)} AND {int(hasta)}"
        proposito = "papers por rango de anios (B+ sobre anio)"
    filas = _ejecutar(motor, proposito, sql, registro).rows

    conteo = _ejecutar(
        motor,
        "chunks por paper (GROUP BY)",
        f"SELECT paper_id, COUNT(*) FROM {tablas.chunks} GROUP BY paper_id",
        registro,
    ).rows
    chunks = {int(paper_id): int(n) for paper_id, n in conteo}

    buscado = (autor or "").strip().lower()
    papers = [
        FichaPaper(int(i), str(a), str(t), str(au), int(an), str(c), str(ab), str(u), chunks.get(int(i), 0))
        for i, a, t, au, an, c, ab, u in filas
        if buscado in str(au).lower()
    ]
    papers.sort(key=lambda p: (-p.anio, p.titulo.lower()))
    return Listado(tuple(papers), tuple(registro))


@dataclass(frozen=True, slots=True)
class Agregado:
    arxiv_id: str
    reporte: Reporte


def agregar(
    motor: ClienteMotor,
    tablas: Tablas,
    carpeta: Path,
    *,
    pdf: bytes,
    titulo: str,
    autores: list[str],
    anio: int,
    abstract: str = "",
) -> Agregado:
    """Guarda el PDF y lo carga como cualquier paper del corpus.

    La carga es atomica por lote: si el PDF no se puede leer, no queda nada
    en las tablas. Al final la carga reconstruye el indice de texto, asi que
    la siguiente pregunta ya lo encuentra sin pagar esa espera.
    """

    if not pdf.startswith(b"%PDF-"):
        raise ValueError("el archivo no es un PDF")
    arxiv_id = f"{PREFIJO_LOCAL}{uuid.uuid4().hex[:12]}"
    paper = Paper(
        arxiv_id=arxiv_id,
        titulo=titulo.strip(),
        autores=tuple(a.strip() for a in autores if a.strip()),
        anio=anio,
        categoria="local",
        abstract=abstract.strip(),
    )
    carpeta.mkdir(parents=True, exist_ok=True)
    (carpeta / paper.archivo).write_bytes(pdf)
    return Agregado(arxiv_id, cargar(motor, [paper], carpeta, tablas=tablas))
