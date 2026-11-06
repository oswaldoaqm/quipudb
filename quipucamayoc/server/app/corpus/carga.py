"""Carga del corpus en QuipuDB (#244): de los PDFs a tablas consultables.

Uso, desde ``quipucamayoc/server`` y con el motor levantado::

    python -m app.corpus.carga             # todo lo descargado
    python -m app.corpus.carga --limite 20

Se puede volver a ejecutar: los papers que ya estan en la tabla se saltan, y
cada lote entra entero o no entra (carga CSV atomica del motor, #114).
"""

from __future__ import annotations

import argparse
import itertools
import sys
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from app.config import load_settings
from app.corpus.arxiv import DATOS, Paper, leer_metadatos
from app.corpus.chunks import Chunk, dividir
from app.corpus.esquema import BYTES_PAPERS, Tablas, chunk_id, recortar
from app.corpus.pdf import PdfError, extraer
from app.motor import ClienteMotor, MotorError

CORPUS = Tablas()
"""Las tablas de verdad, sin prefijo."""

LOTE = 20
"""Papers por carga CSV: pocos para que un fallo cueste poco, bastantes para no
pagar una peticion por paper."""


@dataclass(slots=True)
class Tiempos:
    extraccion_s: float = 0.0
    chunking_s: float = 0.0
    carga_s: float = 0.0
    indice_s: float = 0.0


@dataclass(slots=True)
class Reporte:
    papers: int = 0
    chunks: int = 0
    ya_cargados: int = 0
    fallidos: list[tuple[str, str]] = field(default_factory=list)
    tiempos: Tiempos = field(default_factory=Tiempos)


@dataclass(frozen=True, slots=True)
class _Preparado:
    paper: Paper
    paper_id: int
    chunks: tuple[Chunk, ...]


def fila_paper(paper: Paper, paper_id: int) -> tuple[object, ...]:
    """La fila de ``papers``, con cada texto recortado a su VARCHAR."""

    b = BYTES_PAPERS
    return (
        recortar(paper.arxiv_id, b["arxiv_id"]),
        paper_id,
        recortar(paper.titulo, b["titulo"]),
        recortar(", ".join(paper.autores), b["autores"]),
        paper.anio,
        recortar(paper.categoria, b["categoria"]),
        recortar(paper.abstract, b["abstract"]),
        # Un paper subido a mano (#250) no tiene pagina en arXiv.
        "" if paper.categoria == "local" else recortar(f"https://arxiv.org/abs/{paper.arxiv_id}", b["url"]),
    )


def filas_chunks(paper_id: int, chunks: Iterable[Chunk]) -> list[tuple[object, ...]]:
    return [(chunk_id(paper_id, c.orden), paper_id, c.orden, c.pagina, c.texto) for c in chunks]


def crear_esquema(motor: ClienteMotor, tablas: Tablas) -> bool:
    """Crea tablas e indices si no existen. Devuelve si los creo."""

    existentes = motor.table_names()
    if {tablas.papers, tablas.chunks} <= existentes:
        return False
    if tablas.papers in existentes or tablas.chunks in existentes:
        raise MotorError(
            f"solo existe una de {tablas.papers} y {tablas.chunks}: borra la que quedo "
            "(DROP TABLE) y vuelve a cargar"
        )
    motor.query(";".join(tablas.ddl()))
    return True


def _papers_cargados(motor: ClienteMotor, tablas: Tablas) -> dict[str, int]:
    resultado = motor.query(f"SELECT arxiv_id, id FROM {tablas.papers}")
    return {str(arxiv_id): int(paper_id) for arxiv_id, paper_id in resultado.rows}


def _limpiar_huerfanos(motor: ClienteMotor, tablas: Tablas, papers: set[int]) -> int:
    """Borra los chunks de papers que no llegaron a la tabla de papers.

    Quedan si una carga se corta entre los chunks y los papers de un lote. Sin
    esto, volver a cargar ese paper chocaria con sus propios chunks viejos.
    """

    resultado = motor.query(
        f"SELECT paper_id, COUNT(*) FROM {tablas.chunks} GROUP BY paper_id"
    )
    huerfanos = sorted({int(fila[0]) for fila in resultado.rows} - papers)
    for paper_id in huerfanos:
        motor.query(f"DELETE FROM {tablas.chunks} WHERE paper_id = {paper_id}")
    return len(huerfanos)


def _preparar(
    papers: Sequence[tuple[Paper, int]],
    pdfs: Path,
    reporte: Reporte,
    log: Callable[[str], None],
) -> list[_Preparado]:
    preparados = []
    for paper, paper_id in papers:
        inicio = time.perf_counter()
        try:
            documento = extraer(pdfs / paper.archivo)
        except PdfError as error:
            reporte.fallidos.append((paper.arxiv_id, str(error)))
            log(f"  {paper.arxiv_id}: {error}")
            continue
        finally:
            reporte.tiempos.extraccion_s += time.perf_counter() - inicio
        inicio = time.perf_counter()
        chunks = tuple(dividir(documento.paginas))
        reporte.tiempos.chunking_s += time.perf_counter() - inicio
        if not chunks:
            reporte.fallidos.append((paper.arxiv_id, "el PDF no dejo ningun chunk"))
            continue
        preparados.append(_Preparado(paper, paper_id, chunks))
    return preparados


def _cargar_lote(
    motor: ClienteMotor, tablas: Tablas, lote: Sequence[_Preparado], reporte: Reporte
) -> None:
    filas = [fila for p in lote for fila in filas_chunks(p.paper_id, p.chunks)]
    inicio = time.perf_counter()
    try:
        carga = motor.load_csv(tablas.chunks, tablas.columnas_chunks, filas, atomic=True)
        if carga.failed:
            raise MotorError(f"chunks: {carga.errors[:1]}")
        try:
            carga = motor.load_csv(
                tablas.papers,
                tablas.columnas_papers,
                [fila_paper(p.paper, p.paper_id) for p in lote],
                atomic=True,
            )
            if carga.failed:
                raise MotorError(f"papers: {carga.errors[:1]}")
        except MotorError:
            # Los chunks ya entraron y sus papers no: se sacan para que el lote
            # quede como si nunca se hubiera intentado.
            for p in lote:
                motor.query(f"DELETE FROM {tablas.chunks} WHERE paper_id = {p.paper_id}")
            raise
    except MotorError as error:
        reporte.fallidos.extend((p.paper.arxiv_id, f"el motor rechazo el lote: {error}") for p in lote)
    else:
        reporte.papers += len(lote)
        reporte.chunks += len(filas)
    finally:
        reporte.tiempos.carga_s += time.perf_counter() - inicio


def cargar(
    motor: ClienteMotor,
    papers: Iterable[Paper],
    pdfs: Path,
    *,
    tablas: Tablas = CORPUS,
    lote: int = LOTE,
    log: Callable[[str], None] = lambda _: None,
) -> Reporte:
    """Carga en el motor los ``papers`` que aun no estan, con sus chunks."""

    reporte = Reporte()
    crear_esquema(motor, tablas)
    cargados = _papers_cargados(motor, tablas)
    huerfanos = _limpiar_huerfanos(motor, tablas, set(cargados.values()))
    if huerfanos:
        log(f"se borraron los chunks de {huerfanos} papers que no llegaron a cargarse")

    siguiente = max(cargados.values(), default=0) + 1
    pendientes: list[tuple[Paper, int]] = []
    for paper in papers:
        if paper.arxiv_id in cargados:
            reporte.ya_cargados += 1
            continue
        pendientes.append((paper, siguiente))
        siguiente += 1

    for inicio in range(0, len(pendientes), lote):
        parte = pendientes[inicio : inicio + lote]
        log(f"[{inicio + len(parte)}/{len(pendientes)}] extrayendo y cargando")
        preparados = _preparar(parte, pdfs, reporte, log)
        if preparados:
            _cargar_lote(motor, tablas, preparados, reporte)

    if reporte.chunks:
        # La carga deja el indice de texto desactualizado y la primera busqueda
        # lo reconstruye (#167): se hace aqui, para medirla y para que la
        # primera pregunta del usuario no la pague.
        inicio = time.perf_counter()
        motor.query(f"SELECT id FROM {tablas.chunks} WHERE MATCH(texto, 'index') LIMIT 1")
        reporte.tiempos.indice_s = time.perf_counter() - inicio
    return reporte


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Carga el corpus descargado en QuipuDB")
    parser.add_argument("--datos", type=Path, default=DATOS, help="carpeta de la descarga")
    parser.add_argument("--limite", type=int, default=None, help="solo los primeros N papers")
    args = parser.parse_args(argv)

    papers = list(itertools.islice(leer_metadatos(args.datos), args.limite))
    with ClienteMotor.desde(load_settings()) as motor:
        reporte = cargar(motor, papers, args.datos / "pdf", log=print)
    t = reporte.tiempos
    print(
        f"papers cargados: {reporte.papers}, chunks: {reporte.chunks}, "
        f"ya estaban: {reporte.ya_cargados}, fallidos: {len(reporte.fallidos)}"
    )
    print(
        f"tiempos (s): extraccion {t.extraccion_s:.1f}, chunking {t.chunking_s:.1f}, "
        f"carga {t.carga_s:.1f}, indice {t.indice_s:.1f}"
    )
    for arxiv_id, motivo in reporte.fallidos:
        print(f"  {arxiv_id}: {motivo}")
    return 1 if reporte.fallidos else 0


if __name__ == "__main__":
    sys.exit(main())
