"""Metricas de la evaluacion del RAG (issue #253)."""

import json

import pytest

from benchmarks.scripts.evaluar_rag import (
    PREGUNTAS,
    hit_at,
    mrr,
    papers_en_orden,
    posicion,
    resumen_recuperacion,
)


def test_papers_en_orden_conserva_el_mejor_chunk_de_cada_uno() -> None:
    assert papers_en_orden([7, 3, 7, 9, 3, 1]) == [7, 3, 9, 1]


def test_posicion_es_la_del_primer_esperado() -> None:
    ranking = ["a", "b", "c", "d"]

    assert posicion(ranking, ["c", "b"]) == 2
    assert posicion(ranking, ["z"]) is None


def test_hit_y_mrr_a_mano() -> None:
    posiciones = [1, 3, None, 12]

    assert hit_at(posiciones, 1) == 0.25
    assert hit_at(posiciones, 3) == 0.5
    assert hit_at(posiciones, 10) == 0.5
    assert mrr(posiciones) == pytest.approx((1 + 1 / 3) / 4)  # el 12 queda fuera de @10


def test_sin_preguntas_las_metricas_son_cero() -> None:
    assert hit_at([], 5) == mrr([]) == 0.0


def test_el_resumen_agrupa_por_consulta_y_ranking() -> None:
    filas = [
        {"id": "p1", "consulta": c, "ranking": r, "posicion": p, "tiempo_ms": "10", "tiempo_motor_ms": "4"}
        for c, r, p in [
            ("original", "BM25", "1"),
            ("original", "TF_IDF", ""),
            ("reescrita", "BM25", "2"),
            ("reescrita", "TF_IDF", "1"),
        ]
    ]

    resumen = {(r["consulta"], r["ranking"]): r for r in resumen_recuperacion(filas)}

    assert resumen[("original", "BM25")]["mrr@10"] == 1.0
    assert resumen[("original", "TF_IDF")]["hit@10"] == 0.0
    assert resumen[("reescrita", "BM25")]["mrr@10"] == 0.5


def test_las_preguntas_versionadas_estan_bien_formadas() -> None:
    preguntas = json.loads(PREGUNTAS.read_text(encoding="utf-8"))["preguntas"]

    assert 20 <= len(preguntas) <= 30
    assert len({p["id"] for p in preguntas}) == len(preguntas)
    assert all(p["pregunta"].strip() and p["esperados"] for p in preguntas)


def test_el_resumen_separa_los_modos_y_lee_csv_sin_columna_modo() -> None:
    fila = {"id": "p1", "posicion": "2", "tiempo_ms": "9", "tiempo_motor_ms": "3"}
    filas = [
        {**fila, "consulta": "original", "ranking": "BM25"},  # CSV de antes de #252
        {**fila, "modo": "vectores", "consulta": "original", "ranking": "HNSW", "posicion": "1"},
        {**fila, "modo": "hibrido", "consulta": "reescrita", "ranking": "BM25", "posicion": ""},
    ]

    resumen = {r["modo"]: r for r in resumen_recuperacion(filas)}

    assert set(resumen) == {"texto", "vectores", "hibrido"}
    assert (resumen["texto"]["mrr@10"], resumen["vectores"]["mrr@10"], resumen["hibrido"]["hit@10"]) == (
        0.5, 1.0, 0.0,
    )


def test_el_hibrido_ordena_los_papers_como_la_aplicacion() -> None:
    for modulo in ("pypdf", "httpx", "dotenv"):
        pytest.importorskip(modulo, reason="importa el RAG de quipucamayoc/server")
    from benchmarks.scripts.evaluar_rag import _servidor, ordenar_papers

    _servidor()
    texto = [(10, 1), (11, 2), (12, 3)]
    vectores = [(12, 3), (20, 4)]

    assert ordenar_papers("texto", texto, vectores) == [1, 2, 3]
    assert ordenar_papers("vectores", texto, vectores) == [3, 4]
    # El chunk 12 esta en las dos listas: su paper sube al primer lugar.
    assert ordenar_papers("hibrido", texto, vectores)[0] == 3
