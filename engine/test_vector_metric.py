"""Las metricas de similitud del core vistas desde Python (issue #179).

Las pruebas de los valores estan en `core/tests/index/vector_metric_test.cpp`.
Aqui se comprueba lo que agrega el binding: que la metrica se elige por
consulta con el enum, que acepta listas y arreglos de numpy, y que los errores
llegan como `InvalidRecord`.
"""

from __future__ import annotations

import math

import pytest

quipudb = pytest.importorskip(
    "quipudb_native",
    reason="los bindings no estan compilados: cmake -DQUIPUDB_BUILD_PYTHON=ON",
)

METRICAS = (
    quipudb.VectorMetric.EUCLIDEAN,
    quipudb.VectorMetric.DOT_PRODUCT,
    quipudb.VectorMetric.COSINE,
)


def test_los_tres_valores_de_un_par_conocido() -> None:
    a, b = [1.0, 2.0, 3.0], [4.0, 5.0, 6.0]
    assert quipudb.euclidean_distance(a, b) == pytest.approx(math.sqrt(27))
    assert quipudb.dot_product(a, b) == pytest.approx(32)
    assert quipudb.cosine_similarity(a, b) == pytest.approx(32 / math.sqrt(14 * 77))


def test_la_metrica_se_elige_por_consulta() -> None:
    a, b = [1.0, 1.0], [-1.0, -1.0]
    valores = {m: quipudb.vector_similarity(a, b, m) for m in METRICAS}
    assert valores[quipudb.VectorMetric.EUCLIDEAN] == pytest.approx(math.sqrt(8))
    assert valores[quipudb.VectorMetric.DOT_PRODUCT] == pytest.approx(-2)
    assert valores[quipudb.VectorMetric.COSINE] == pytest.approx(-1)


def test_vector_distance_y_from_distance_son_inversas() -> None:
    a, b = [1.0, 2.0, 3.0], [-2.0, 0.5, 4.0]
    for m in METRICAS:
        d = quipudb.vector_distance(a, b, m)
        assert quipudb.from_distance(d, m) == pytest.approx(quipudb.vector_similarity(a, b, m))


def test_menor_distancia_es_siempre_el_mas_parecido() -> None:
    consulta, casi_igual, lejos = [1.0, 1.0, 0.0], [1.0, 0.9, 0.0], [-1.0, 0.0, 1.0]
    for m in METRICAS:
        assert quipudb.vector_distance(consulta, casi_igual, m) < quipudb.vector_distance(
            consulta, lejos, m
        )


def test_sentido_de_cada_metrica() -> None:
    assert not quipudb.higher_is_better(quipudb.VectorMetric.EUCLIDEAN)
    assert quipudb.higher_is_better(quipudb.VectorMetric.DOT_PRODUCT)
    assert quipudb.higher_is_better(quipudb.VectorMetric.COSINE)


def test_acepta_arreglos_de_numpy() -> None:
    np = pytest.importorskip("numpy")
    a = np.array([1, 2, 3], dtype=np.float32)
    b = np.array([4, 5, 6], dtype=np.float32)
    assert quipudb.dot_product(a, b) == pytest.approx(32)


def test_coseno_con_norma_cero_vale_cero() -> None:
    assert quipudb.cosine_similarity([0.0, 0.0], [1.0, 2.0]) == 0.0
    assert quipudb.vector_distance([0.0, 0.0], [1.0, 2.0], quipudb.VectorMetric.COSINE) == 1.0


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ([1.0, 2.0, 3.0], [1.0, 2.0]),
        ([], []),
        ([1.0, float("nan")], [1.0, 2.0]),
        ([1.0, 2.0], [float("inf"), 2.0]),
    ],
)
def test_vectores_invalidos_son_invalid_record(a, b) -> None:
    for m in METRICAS:
        with pytest.raises(quipudb.InvalidRecord):
            quipudb.vector_distance(a, b, m)
