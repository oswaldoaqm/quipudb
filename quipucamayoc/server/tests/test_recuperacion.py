"""Recuperacion de chunks y papers en QuipuDB (#245)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.corpus.arxiv import Paper
from app.corpus.carga import cargar
from app.corpus.esquema import Tablas
from app.rag.recuperacion import Fragmento, recuperar, seleccionar, sql_match
from tests.pdfs import escribir


def _pasos(paso: dict, op: str) -> list[dict]:
    """Los pasos ``op`` del plan, a cualquier profundidad."""
    propios = [paso] if paso["op"] == op else []
    return propios + [p for hijo in paso["children"] for p in _pasos(hijo, op)]


def _f(chunk_id: int, paper_id: int, relevancia: float) -> Fragmento:
    return Fragmento(chunk_id, paper_id, 0, 1, f"texto {chunk_id}", relevancia)


def test_seleccionar_respeta_el_tope_por_paper_y_el_orden() -> None:
    fragmentos = [_f(1, 1, 9), _f(2, 1, 8), _f(3, 1, 7), _f(4, 2, 6), _f(5, 1, 5), _f(6, 3, 4)]

    elegidos = seleccionar(fragmentos, k=4, max_por_paper=2)

    assert [f.chunk_id for f in elegidos] == [1, 2, 4, 6]


def test_seleccionar_con_pocos_candidatos_devuelve_los_que_hay() -> None:
    assert seleccionar([_f(1, 1, 1)], k=8, max_por_paper=3) == [_f(1, 1, 1)]


def test_el_sql_escapa_la_consulta_y_valida_el_ranking() -> None:
    sql = sql_match("O'Neil B-tree", "TF_IDF", 24, Tablas("t_"))

    assert sql == (
        "SELECT id, paper_id, orden, pagina, texto, SCORE() AS relevancia FROM t_chunks "
        "WHERE MATCH(texto, 'O''Neil B-tree') USING TF_IDF LIMIT 24"
    )
    with pytest.raises(ValueError, match="ranking desconocido"):
        sql_match("x", "COSENO", 5)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Contra el motor real
# ---------------------------------------------------------------------------

_TEMAS = {
    "2401.00001v1": (
        "Graph indexes",
        [
            "Hierarchical navigable small world graphs answer nearest neighbor queries.",
            "Greedy routing descends the layers of the graph to reach the query.",
            "Graph indexes trade memory for high recall at low latency.",
        ],
    ),
    "2401.00002v1": (
        "Inverted files",
        [
            "Inverted file indexes cluster vectors around centroids with k-means.",
            "A query probes the nprobe closest lists to find candidates.",
            "Product quantization compresses the vectors stored in each list.",
        ],
    ),
    "2401.00003v1": (
        "Query optimization",
        [
            "Cost based optimizers choose join orders with cardinality estimates.",
            "Histograms summarize column distributions for selectivity.",
            "Dynamic programming enumerates plans for small join graphs.",
        ],
    ),
}


@pytest.fixture()
def corpus(motor_real, tmp_path: Path):
    motor, prefijo = motor_real
    tablas = Tablas(prefijo)
    papers = []
    for arxiv_id, (titulo, oraciones) in _TEMAS.items():
        paper = Paper(arxiv_id, titulo, ("Ada Lovelace",), 2020, "cs.DB", "Resumen.")
        papers.append(paper)
        # Dos paginas por paper, cada una un poco distinta: varios chunks.
        escribir(
            tmp_path / paper.archivo,
            [[f"{titulo} page one.", *oraciones], [f"{titulo} page two.", *reversed(oraciones)]],
        )
    reporte = cargar(motor, papers, tmp_path, tablas=tablas)
    assert reporte.fallidos == []
    return motor, tablas


def test_recupera_los_chunks_del_paper_que_responde(corpus) -> None:
    motor, tablas = corpus

    r = recuperar(motor, "graph layers greedy routing", tablas=tablas)

    assert r.evidencias
    primera = r.evidencias[0]
    assert primera.paper.arxiv_id == "2401.00001v1"
    assert primera.paper.titulo == "Graph indexes"
    assert primera.paper.autores == "Ada Lovelace"
    assert "greedy routing" in primera.fragmento.texto.lower()
    relevancias = [e.fragmento.relevancia for e in r.evidencias]
    assert relevancias == sorted(relevancias, reverse=True)


def test_deja_constancia_de_cada_sql_con_su_plan(corpus) -> None:
    motor, tablas = corpus

    r = recuperar(motor, "inverted file centroids", ranking="TF_IDF", tablas=tablas)

    match, *papers = r.consultas
    assert "USING TF_IDF" in match.sql
    [texto] = _pasos(match.plan["root"], "text_search")
    assert (texto["op"], texto["ranking"]) == ("text_search", "tf_idf")
    assert "idioma english" in texto["detail"]
    assert papers and all(_pasos(c.plan["root"], "index_search") for c in papers)
    assert len(papers) == len({e.paper.id for e in r.evidencias})
    assert r.tiempo_ms > 0


def test_el_tope_por_paper_se_cumple_con_el_motor(corpus) -> None:
    motor, tablas = corpus

    r = recuperar(motor, "graph", k=5, max_por_paper=1, tablas=tablas)

    ids = [e.paper.id for e in r.evidencias]
    assert len(ids) == len(set(ids))


def test_sin_coincidencias_o_sin_consulta_no_hay_evidencias(corpus) -> None:
    motor, tablas = corpus

    assert recuperar(motor, "helicopter", tablas=tablas).evidencias == ()
    vacia = recuperar(motor, "   ", tablas=tablas)
    assert (vacia.evidencias, vacia.consultas) == ((), ())


def test_una_pregunta_hostil_no_rompe_nada(corpus) -> None:
    motor, tablas = corpus

    recuperar(motor, "'; DROP TABLE papers; -- graph", tablas=tablas)

    assert {tablas.papers, tablas.chunks} <= motor.table_names()
