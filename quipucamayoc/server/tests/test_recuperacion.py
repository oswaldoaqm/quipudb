"""Recuperacion de chunks y papers en QuipuDB (#245)."""

from __future__ import annotations

import pytest

from app.corpus.esquema import Tablas
from app.rag.recuperacion import Fragmento, recuperar, seleccionar, sql_match


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


# ---------------------------------------------------------------------------
# Vectores e hibrido (#252)
# ---------------------------------------------------------------------------


def test_rrf_premia_estar_en_las_dos_listas() -> None:
    from app.rag.recuperacion import RRF_K, fusionar

    fusion = fusionar([[1, 2, 3], [3, 4]])

    assert [c for c, _ in fusion] == [3, 1, 2, 4]
    assert dict(fusion)[3] == pytest.approx(1 / (RRF_K + 3) + 1 / (RRF_K + 1))


def test_rrf_desempata_por_la_mejor_posicion_y_el_id() -> None:
    from app.rag.recuperacion import fusionar

    assert [c for c, _ in fusionar([[7, 9], [9, 7]])] == [7, 9]
    assert [c for c, _ in fusionar([[], [5]])] == [5]


def test_sin_vector_los_modos_vectoriales_son_error() -> None:
    with pytest.raises(ValueError, match="embedding"):
        recuperar(None, "x", modo="hibrido")  # type: ignore[arg-type]


def test_modo_vectores_busca_por_significado_con_hnsw(corpus_vectorial) -> None:
    from tests.llm_falso import EmbedderFalso

    motor, tablas = corpus_vectorial
    vector = EmbedderFalso().consulta("centroids k-means nprobe lists")

    r = recuperar(motor, "", modo="vectores", vector=vector, tablas=tablas)

    assert r.modo == "vectores"
    assert r.evidencias[0].paper.arxiv_id == "2401.00002v1"
    assert r.evidencias[0].fragmento.origen == ("vectores",)
    assert "centroids" in r.evidencias[0].fragmento.texto  # el texto vino por el hash
    similar, *resto = r.consultas
    assert "SIMILAR_TO(embedding, VECTOR(" in similar.sql
    knn = _pasos(similar.plan["root"], "knn_search")[0]
    assert knn["structure"] == "hnsw"
    assert "consulta VECTOR literal de 768 componentes" in knn["detail"]
    textos = [c for c in resto if c.proposito.startswith("texto del chunk")]
    assert textos and all(_pasos(c.plan["root"], "index_search") for c in textos)


def test_modo_hibrido_fusiona_y_dice_de_donde_salio_cada_chunk(corpus_vectorial) -> None:
    from tests.llm_falso import EmbedderFalso

    motor, tablas = corpus_vectorial
    # BM25 encuentra "histograms"; el embedding falso, "join orders".
    vector = EmbedderFalso().consulta("join orders cardinality")

    r = recuperar(motor, "histograms selectivity", modo="hibrido", vector=vector, tablas=tablas)

    origenes = {f.origen for f in (e.fragmento for e in r.evidencias)}
    assert ("texto", "vectores") in origenes or {("texto",), ("vectores",)} <= origenes
    assert r.evidencias[0].paper.arxiv_id == "2401.00003v1"
    relevancias = [e.fragmento.relevancia for e in r.evidencias]
    assert relevancias == sorted(relevancias, reverse=True)
    assert [c.proposito.split(" (")[0] for c in r.consultas[:2]] == [
        "chunks por palabras", "chunks por significado",
    ]
