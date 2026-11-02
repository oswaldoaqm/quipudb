"""Las formulas TF-IDF del histograma visual (issue #181).

Son las MISMAS que usa la busqueda de texto de la Parte 3, definidas en
``core/include/quipudb/index/text_weights.hpp``:

    idf(w) = log10(N / df_w)
    tf_w   = 1 + log10(tf)   (log, la de la diapositiva)   o   tf   (raw)

Estas pruebas existen para que no se separen. Si el histograma visual pesara
distinto que el textual, los numeros del informe no se podrian comparar entre
las partes 3 y 4, y nadie se enteraria hasta escribirlo.

No necesitan OpenCV ni soundfile: son funciones puras sobre numpy.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from engine.multimedia.weights import (
    TF_LOG,
    TF_RAW,
    WeightError,
    inverse_document_frequency,
    tf_weight,
)

# ---------------------------------------------------------------------------
# tf
# ---------------------------------------------------------------------------


def test_la_variante_log_es_uno_mas_log10() -> None:
    assert tf_weight(np.array([1, 10, 100]), TF_LOG).tolist() == [1.0, 2.0, 3.0]


def test_la_variante_raw_es_la_frecuencia_tal_cual() -> None:
    assert tf_weight(np.array([1, 10, 100]), TF_RAW).tolist() == [1.0, 10.0, 100.0]


def test_una_palabra_que_no_aparece_pesa_cero_y_no_menos_infinito() -> None:
    # 1 + log10(0) seria -inf, y de ahi en adelante todo el vector queda
    # inservible. Es la misma guarda que tiene `tf_weight` en C++.
    pesos = tf_weight(np.array([0, 0, 5]), TF_LOG)
    assert pesos[0] == 0.0
    assert np.isfinite(pesos).all()


def test_la_variante_log_crece_mas_despacio_que_la_cruda() -> None:
    # Es la razon de ser de la variante: diez apariciones de una palabra no
    # hacen al objeto diez veces mas parecido.
    assert tf_weight(np.array([10]), TF_LOG)[0] < tf_weight(np.array([10]), TF_RAW)[0]


def test_la_variante_log_es_monotona() -> None:
    pesos = tf_weight(np.arange(1, 50), TF_LOG)
    assert np.all(np.diff(pesos) > 0)


def test_una_variante_desconocida_se_rechaza() -> None:
    with pytest.raises(WeightError, match="variante de tf"):
        tf_weight(np.array([1]), "sublineal")


# ---------------------------------------------------------------------------
# idf
# ---------------------------------------------------------------------------


def test_el_idf_es_log10_de_n_sobre_df() -> None:
    assert inverse_document_frequency(np.array([2]), 10)[0] == pytest.approx(math.log10(5.0))


def test_una_palabra_en_todos_los_objetos_no_pesa() -> None:
    # No distingue a ninguno, igual que la palabra "de" en texto.
    assert inverse_document_frequency(np.array([12]), 12)[0] == 0.0


def test_una_palabra_en_ningun_objeto_pesa_cero_y_no_divide_por_cero() -> None:
    assert inverse_document_frequency(np.array([0]), 12)[0] == 0.0


def test_un_dataset_vacio_no_divide_por_cero() -> None:
    assert inverse_document_frequency(np.array([0, 3]), 0).tolist() == [0.0, 0.0]


def test_cuanto_mas_rara_es_la_palabra_mas_pesa() -> None:
    pesos = inverse_document_frequency(np.array([1, 2, 5, 10]), 10)
    assert np.all(np.diff(pesos) < 0)


def test_con_un_solo_objeto_ninguna_palabra_distingue() -> None:
    # Caso degenerado pero real: con N = 1 todas las palabras estan en el
    # unico objeto, asi que el vector entero sale nulo. No es un defecto, es
    # lo que significa el idf; la senal es que hace falta mas dataset.
    assert inverse_document_frequency(np.array([1, 1, 1]), 1).tolist() == [0.0, 0.0, 0.0]
