"""Pruebas de Precision@10 y Recall@10 (#173), con casos chicos calculados a mano."""

from __future__ import annotations

import csv
import subprocess
import sys
from pathlib import Path

import pytest

from benchmarks.scripts.cargar_documentos import ConsultaTexto, leer_consultas, leer_documentos
from benchmarks.scripts.generar_documentos import generar, leer_quora
from benchmarks.scripts.relevancia_texto import (
    Ranking,
    comparar,
    escribir_rankings,
    evaluar,
    evaluar_archivos,
    leer_rankings,
    precision_at_k,
    prueba_pareada,
    recall_at_k,
    recortar,
    resumir,
    techo_precision,
)
from benchmarks.test_generar_documentos import _zip_sintetico

SCRIPT = Path(__file__).resolve().parent / "scripts" / "relevancia_texto.py"
DIEZ = list(range(1, 11))


# ---------------------------------------------------------------------------
# Precision@10 y Recall@10
# ---------------------------------------------------------------------------


def test_precision_y_recall_de_un_top_10_completo():
    # Relevantes 2, 5 y 11: entran 2 y 5, el 11 quedo afuera del top-10.
    assert precision_at_k(DIEZ, {2, 5, 11}) == pytest.approx(2 / 10)
    assert recall_at_k(DIEZ, {2, 5, 11}) == pytest.approx(2 / 3)


def test_todos_los_relevantes_arriba():
    # Un solo relevante, primero: Recall 1, pero Precision solo 0,1. Es el techo
    # de una consulta con un relevante, la mayoria en Quora.
    assert precision_at_k(DIEZ, {1}) == pytest.approx(0.1)
    assert recall_at_k(DIEZ, {1}) == 1.0
    assert techo_precision(1) == pytest.approx(0.1)
    assert techo_precision(10) == 1.0
    assert techo_precision(12) == 1.0


def test_un_ranking_corto_no_achica_el_denominador():
    # Devuelve 2 documentos, uno relevante: 1/10, no 1/2.
    assert precision_at_k([7, 3], {3}) == pytest.approx(0.1)
    assert recall_at_k([7, 3], {3}) == 1.0
    assert precision_at_k([], {3}) == 0.0
    assert recall_at_k([], {3}) == 0.0


def test_solo_cuentan_los_k_primeros_y_sin_repetir():
    # P@3 de [4, 1, 2, 3] con relevantes {1, 2, 3}: el 3 esta en el puesto 4.
    assert precision_at_k([4, 1, 2, 3], {1, 2, 3}, k=3) == pytest.approx(2 / 3)
    assert recall_at_k([4, 1, 2, 3], {1, 2, 3}, k=3) == pytest.approx(2 / 3)
    assert precision_at_k([1, 1, 1], {1}, k=3) == pytest.approx(1 / 3)


def test_recall_sin_relevantes_no_esta_definido():
    with pytest.raises(ValueError, match="sin relevantes"):
        recall_at_k(DIEZ, set())
    with pytest.raises(ValueError, match="k debe ser"):
        precision_at_k(DIEZ, {1}, k=0)


# ---------------------------------------------------------------------------
# Empates en el corte
# ---------------------------------------------------------------------------


def test_recortar_se_queda_con_los_empatados_con_el_decimo():
    resultados = [(d, 10.0 - d) for d in range(1, 9)]  # 8 con puntajes distintos
    resultados += [(d, 1.0) for d in range(9, 14)]  # 5 empatados: del 9.o al 13.o
    resultados += [(14, 0.5)]
    assert [d for d, _ in recortar(resultados)] == list(range(1, 14))
    sin_empate = [(d, 20.0 - d) for d in range(1, 15)]
    assert [d for d, _ in recortar(sin_empate)] == DIEZ
    assert recortar(sin_empate[:4]) == sin_empate[:4]


def test_aciertos_esperados_reparten_los_lugares_del_grupo_empatado():
    # 8 documentos arriba (uno relevante, el 3) y un grupo de 4 empatados para
    # los 2 lugares que quedan, con un relevante (el 11).
    # Esperado: 1 + 2 * 1/4 = 1,5. Con el desempate por orden: entran 9 y 10,
    # ninguno relevante, asi que 1.
    documentos = tuple(range(1, 13))
    puntajes = tuple([9.0, 8, 7, 6, 5, 4, 3, 2] + [1.0] * 4)
    ranking = Ranking(documentos, puntajes)
    assert ranking.empate_en_corte()
    assert ranking.aciertos_esperados({3, 11}) == pytest.approx(1.5)
    assert ranking.aciertos_esperados({3, 9}) == pytest.approx(1.5)

    # Un grupo empatado entero dentro del top-10 no cambia nada.
    sin_corte = Ranking(tuple(range(1, 6)), (1.0,) * 5)
    assert not sin_corte.empate_en_corte()
    assert sin_corte.aciertos_esperados({2, 7}) == 1.0


def test_el_grupo_puede_empezar_antes_del_decimo():
    # Todos empatados: 10 lugares para 20 documentos con 4 relevantes, 2 esperados.
    ranking = Ranking(tuple(range(1, 21)), (3.0,) * 20)
    assert ranking.aciertos_esperados({1, 5, 15, 20}) == pytest.approx(2.0)


# ---------------------------------------------------------------------------
# Archivo de rankings
# ---------------------------------------------------------------------------


def _escribir(ruta: Path, filas) -> Path:
    return escribir_rankings(ruta, filas)


def test_el_archivo_conserva_puntajes_iguales(tmp_path):
    # 0.1 + 0.2 no es 0.3: con repr los dos puntajes iguales siguen iguales.
    puntaje = 0.1 + 0.2
    filas = [("bm25", 50, 1, p, p, puntaje) for p in range(1, 12)]
    rankings = leer_rankings(_escribir(tmp_path / "r.csv", filas))
    assert rankings[("bm25", 50)][1].puntajes == (puntaje,) * 11


@pytest.mark.parametrize(
    "filas, mensaje",
    [
        ([("x", 9, 1, 1, 5, 2.0), ("x", 9, 1, 3, 6, 1.0)], "posiciones"),
        ([("x", 9, 1, 1, 5, 1.0), ("x", 9, 1, 2, 6, 2.0)], "no bajan"),
        ([("x", 9, 1, 1, 5, 2.0), ("x", 9, 1, 2, 5, 1.0)], "dos veces"),
        (
            [("x", 9, 1, p, p, 20.0 - p) for p in range(1, 12)],
            "solo van empatados",
        ),
    ],
)
def test_rechaza_rankings_mal_formados(tmp_path, filas, mensaje):
    with pytest.raises(ValueError, match=mensaje):
        leer_rankings(_escribir(tmp_path / "r.csv", filas))


def test_rechaza_cabecera_y_valores(tmp_path):
    ruta = tmp_path / "r.csv"
    with pytest.raises(ValueError, match="inexistentes"):
        leer_rankings(ruta)
    ruta.write_text("a,b\n", encoding="utf-8")
    with pytest.raises(ValueError, match="cabecera"):
        leer_rankings(ruta)
    ruta.write_text(
        "tecnica,n_documentos,consulta,posicion,documento,puntaje\nx,9,1,1,uno,2.0\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="no numericos"):
        leer_rankings(ruta)


# ---------------------------------------------------------------------------
# Evaluacion y promedios
# ---------------------------------------------------------------------------

CONSULTAS = (
    ConsultaTexto(1, "1", "oro"),
    ConsultaTexto(2, "1", "plata"),
    ConsultaTexto(3, "3", "oro plata cobre"),
)
RELEVANCIA = {1: frozenset({1}), 2: frozenset({2, 3}), 3: frozenset({4})}


def test_promedia_por_tecnica_tamano_y_longitud():
    rankings = {
        1: Ranking((1, 9), (2.0, 1.0)),  # el relevante primero: P 0,1, R 1
        2: Ranking((3, 8), (2.0, 1.0)),  # uno de dos: P 0,1, R 0,5
        # La consulta 3 no encontro nada: P 0, R 0.
    }
    evaluaciones = evaluar("bm25", 50, rankings, CONSULTAS, RELEVANCIA)
    assert [e.aciertos for e in evaluaciones] == [1, 1, 0]

    una, tres = resumir(evaluaciones)
    assert (una["longitud"], una["consultas"]) == ("1", 2)
    assert una["precision_10"] == pytest.approx((0.1 + 0.1) / 2)
    assert una["recall_10"] == pytest.approx((1.0 + 0.5) / 2)
    # Techo: un relevante da 0,1 y dos dan 0,2.
    assert una["techo_precision_10"] == pytest.approx((0.1 + 0.2) / 2)
    assert una["consultas_con_acierto"] == 2
    assert (tres["longitud"], tres["precision_10"], tres["recall_10"]) == ("3", 0.0, 0.0)
    assert tres["consultas_con_acierto"] == 0


def test_rechaza_consultas_desconocidas_o_sin_juicios():
    with pytest.raises(ValueError, match="desconocidas"):
        evaluar("bm25", 50, {99: Ranking((1,), (1.0,))}, CONSULTAS, RELEVANCIA)
    with pytest.raises(ValueError, match="sin juicios|no tiene juicios"):
        evaluar("bm25", 50, {}, CONSULTAS, {1: frozenset({1})})


# ---------------------------------------------------------------------------
# Prueba pareada
# ---------------------------------------------------------------------------


def test_sin_diferencias_el_p_valor_es_uno():
    assert prueba_pareada([0.1, 0.2, 0.0], [0.1, 0.2, 0.0]) == 1.0


def test_una_sola_consulta_distinta_no_es_evidencia():
    # Con una diferencia, cambiarla de signo da siempre la misma magnitud: p = 1.
    assert prueba_pareada([0.1, 0.0, 0.0], [0.0, 0.0, 0.0], 500) == 1.0


def test_veinte_consultas_a_favor_es_significativo_y_simetrico():
    # Los 20 signos iguales: probabilidad 2 / 2**20 bajo la hipotesis nula.
    a, b = [0.1] * 20, [0.0] * 20
    p = prueba_pareada(a, b)
    assert p < 0.001
    assert prueba_pareada(b, a) == p


def test_comparar_exige_las_mismas_consultas():
    e_a = evaluar("a", 50, {1: Ranking((1,), (1.0,))}, CONSULTAS, RELEVANCIA)
    e_b = evaluar("b", 50, {}, CONSULTAS, RELEVANCIA)
    filas = comparar(e_a + e_b, 200)
    una = [f for f in filas if f["longitud"] == "1" and f["metrica"] == "recall_10"]
    assert len(una) == 1
    assert una[0]["tecnica_a"] == "a"
    assert una[0]["diferencia_media"] == pytest.approx(0.5)
    assert una[0]["consultas_distintas"] == 1
    with pytest.raises(ValueError, match="mismas consultas"):
        comparar(e_a + e_b[:1], 200)


# ---------------------------------------------------------------------------
# De punta a punta, sin bindings
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def generado(tmp_path_factory):
    raiz = tmp_path_factory.mktemp("relevancia")
    generar(leer_quora(_zip_sintetico(raiz / "quora.zip")), raiz / "datasets", (300,), preguntas=20)
    return raiz / "datasets"


def _rankings_perfectos(generado: Path, ruta: Path) -> Path:
    """Un ranking que pone primero los relevantes de cada consulta."""
    with (generado / "relevancia_300.csv").open(encoding="utf-8") as archivo:
        filas = list(csv.reader(archivo))[1:]
    por_consulta: dict[int, list[int]] = {}
    for consulta, documento in filas:
        por_consulta.setdefault(int(consulta), []).append(int(documento))
    return escribir_rankings(
        ruta,
        (
            ("perfecto", 300, consulta, posicion, documento, 10.0 - posicion)
            for consulta, docs in por_consulta.items()
            for posicion, documento in enumerate(docs, start=1)
        ),
    )


def test_un_ranking_perfecto_llega_al_techo(generado, tmp_path):
    evaluaciones = evaluar_archivos([_rankings_perfectos(generado, tmp_path / "r.csv")], generado)
    for fila in resumir(evaluaciones):
        assert fila["recall_10"] == 1.0
        assert fila["precision_10"] == pytest.approx(fila["techo_precision_10"])


def test_cli_escribe_tres_csv_y_no_los_pisa(generado, tmp_path):
    rankings = _rankings_perfectos(generado, tmp_path / "x_rankings.csv")
    comando = [sys.executable, "-B", str(SCRIPT), str(rankings), "--datasets", str(generado)]
    comando += ["--permutaciones", "100"]
    salida = subprocess.run(comando, cwd=tmp_path, check=True, capture_output=True, text=True)
    assert "| perfecto | 300 | 1 |" in salida.stdout
    assert {ruta.name for ruta in tmp_path.glob("x_relevancia_*.csv")} == {
        "x_relevancia_por_consulta.csv",
        "x_relevancia_resumen.csv",
        "x_relevancia_pareada.csv",
    }
    otra = subprocess.run(comando, cwd=tmp_path, check=False, capture_output=True, text=True)
    assert otra.returncode == 1
    assert "ya existen" in otra.stderr


# ---------------------------------------------------------------------------
# Con los bindings: los rankings reales de QuipuDB
# ---------------------------------------------------------------------------


def test_integracion_rankings_de_quipudb(generado, tmp_path):
    nativo = pytest.importorskip(
        "quipudb_native",
        reason="requiere bindings compilados para este Python y configurados en PYTHONPATH",
    )
    from benchmarks.scripts.rankings_texto import rankings_quipudb

    dataset = leer_documentos(generado / "documentos_300.csv")
    consultas = leer_consultas(generado / "consultas.csv")
    (tmp_path / "db").mkdir()
    ruta = escribir_rankings(
        tmp_path / "r.csv", rankings_quipudb(nativo, dataset, consultas, tmp_path / "db")
    )
    # leer_rankings valida orden, posiciones y que despues del 10.o solo haya
    # empatados.
    rankings = leer_rankings(ruta)
    assert set(rankings) == {("tf_idf", 300), ("bm25", 300)}
    resumen = {
        (f["tecnica"], f["longitud"]): f for f in resumir(evaluar_archivos([ruta], generado))
    }
    for tecnica in ("tf_idf", "bm25"):
        # En el corpus sintetico cada pregunta comparte cuatro palabras propias
        # con su duplicado: la consulta de 5+ palabras siempre lo encuentra. (La
        # pregunta encadenada tiene otro relevante sin palabras en comun, asi que
        # Recall@10 puede no ser 1; un acierto por consulta, si.)
        larga = resumen[(tecnica, "5+")]
        assert larga["consultas_con_acierto"] == larga["consultas"]
        assert larga["precision_10"] == pytest.approx(0.1)
