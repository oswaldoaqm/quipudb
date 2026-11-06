"""Carga del corpus en QuipuDB (#244)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.corpus.arxiv import Paper
from app.corpus.carga import cargar, fila_paper, filas_chunks
from app.corpus.chunks import Chunk
from app.corpus.esquema import BYTES_PAPERS, Tablas, chunk_id, recortar
from app.motor import literal
from tests.pdfs import escribir


def _paper(arxiv_id: str, titulo: str = "HNSW graphs", autores=("Malkov", "Yashunin")) -> Paper:
    return Paper(
        arxiv_id=arxiv_id,
        titulo=titulo,
        autores=tuple(autores),
        anio=2018,
        categoria="cs.DS",
        abstract="We present a graph index.",
    )


# ---------------------------------------------------------------------------
# Filas, sin motor
# ---------------------------------------------------------------------------


def test_recortar_respeta_bytes_y_caracteres() -> None:
    assert recortar("ñandú", 100) == "ñandú"
    assert recortar("ñandú", 3) == "ña"  # ñ son 2 bytes; la ú no entra
    assert recortar("ññ", 3) == "ñ"


def test_la_fila_del_paper_cabe_en_sus_varchar() -> None:
    paper = _paper("1603.09320v4", titulo="T" * 500, autores=[f"Autor Número {i}" for i in range(100)])

    fila = fila_paper(paper, 7)

    arxiv_id, paper_id, titulo, autores, anio, _, _, url = fila
    assert (arxiv_id, paper_id, anio) == ("1603.09320v4", 7, 2018)
    assert len(titulo.encode()) == BYTES_PAPERS["titulo"]
    assert len(autores.encode()) <= BYTES_PAPERS["autores"]
    assert autores.startswith("Autor Número 0, Autor Número 1")
    assert url == "https://arxiv.org/abs/1603.09320v4"


def test_los_ids_de_chunk_son_deterministas_y_no_chocan_entre_papers() -> None:
    chunks = [Chunk(0, 1, "a"), Chunk(1, 2, "b")]

    assert filas_chunks(3, chunks) == [(30_000, 3, 0, 1, "a"), (30_001, 3, 1, 2, "b")]
    assert chunk_id(4, 0) == chunk_id(3, 9_999) + 1
    with pytest.raises(ValueError):
        chunk_id(1, 10_000)


# ---------------------------------------------------------------------------
# Contra el motor real
# ---------------------------------------------------------------------------

_PAGINA = [
    "Hierarchical navigable small world graphs index vectors for approximate search.",
    "Each layer keeps fewer nodes, so greedy routing descends quickly to the target.",
    "Compared with inverted file indexes, the graph reaches higher recall at equal latency.",
]


def _pdfs(carpeta: Path, papers: list[Paper], rotos: set[str] = frozenset()) -> Path:
    carpeta.mkdir(parents=True, exist_ok=True)
    for numero, paper in enumerate(papers):
        ruta = carpeta / paper.archivo
        if paper.arxiv_id in rotos:
            ruta.write_text("no es un pdf", encoding="utf-8")
            continue
        tema = f"Topic{numero} studies {paper.titulo}."
        escribir(ruta, [[tema, *_PAGINA], [f"Second page of topic{numero}.", *_PAGINA]])
    return carpeta


def test_carga_papers_y_chunks_y_deja_el_indice_listo(motor_real, tmp_path: Path) -> None:
    motor, prefijo = motor_real
    tablas = Tablas(prefijo)
    papers = [_paper("2401.00001v1", "graphs"), _paper("2401.00002v2", "quantization"), _paper("2401.00003v1")]
    pdfs = _pdfs(tmp_path / "pdf", papers, rotos={"2401.00003v1"})

    reporte = cargar(motor, papers, pdfs, tablas=tablas, lote=1)

    assert (reporte.papers, reporte.ya_cargados) == (2, 0)
    assert reporte.chunks == 2  # cada PDF de prueba es un solo chunk
    assert [arxiv_id for arxiv_id, _ in reporte.fallidos] == ["2401.00003v1"]
    assert "no es un PDF legible" in reporte.fallidos[0][1]
    assert reporte.tiempos.indice_s > 0

    filas = motor.query(f"SELECT arxiv_id, id, autores FROM {tablas.papers}").rows
    assert sorted(filas) == [("2401.00001v1", 1, "Malkov, Yashunin"), ("2401.00002v2", 2, "Malkov, Yashunin")]
    encontrado = motor.query(
        f"SELECT paper_id, SCORE() AS relevancia FROM {tablas.chunks} "
        f"WHERE MATCH(texto, {literal('topic1 quantization')}) USING BM25 LIMIT 1"
    )
    assert encontrado.rows[0][0] == 2

    # Buscar un paper por id usa el hash (ADR 0010): el plan lo dice.
    plan = motor.query(f"SELECT titulo FROM {tablas.papers} WHERE id = 2").plan
    assert plan["root"]["children"][0]["children"][0]["op"] == "index_search"


def test_volver_a_cargar_no_duplica_y_solo_agrega_lo_nuevo(motor_real, tmp_path: Path) -> None:
    motor, prefijo = motor_real
    tablas = Tablas(prefijo)
    viejos = [_paper("2401.00001v1"), _paper("2401.00002v1")]
    nuevo = _paper("2401.00009v1")
    pdfs = _pdfs(tmp_path / "pdf", [*viejos, nuevo])
    cargar(motor, viejos, pdfs, tablas=tablas)

    reporte = cargar(motor, [*viejos, nuevo], pdfs, tablas=tablas)

    assert (reporte.papers, reporte.ya_cargados, reporte.fallidos) == (1, 2, [])
    ids = sorted(motor.query(f"SELECT id FROM {tablas.papers}").rows)
    assert ids == [(1,), (2,), (3,)]
    chunks = motor.query(
        f"SELECT paper_id, COUNT(*) FROM {tablas.chunks} GROUP BY paper_id"
    ).rows
    assert sorted(chunks) == [(1, 1), (2, 1), (3, 1)]


def test_los_chunks_huerfanos_de_una_carga_cortada_se_limpian(motor_real, tmp_path: Path) -> None:
    motor, prefijo = motor_real
    tablas = Tablas(prefijo)
    paper = _paper("2401.00001v1")
    pdfs = _pdfs(tmp_path / "pdf", [paper])
    cargar(motor, [], pdfs, tablas=tablas)  # solo crea el esquema
    # Lo que deja una carga cortada entre los chunks y su paper.
    motor.query(f"INSERT INTO {tablas.chunks} VALUES ({chunk_id(1, 0)}, 1, 0, 1, 'resto viejo')")

    reporte = cargar(motor, [paper], pdfs, tablas=tablas)

    assert (reporte.papers, reporte.fallidos) == (1, [])
    textos = [fila[0] for fila in motor.query(f"SELECT texto FROM {tablas.chunks}").rows]
    assert textos and "resto viejo" not in textos
