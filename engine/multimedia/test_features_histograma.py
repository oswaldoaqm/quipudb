"""El histograma de palabras: contar, pesar y normalizar (issue #181).

Es el criterio 1. Lo que se comprueba aqui no es que salga "un vector", sino
que ese vector signifique lo que dice: que la palabra que mas aparece pese
mas, que una palabra inutil no pese, y que dos objetos parecidos queden
cerca.

No necesitan OpenCV ni soundfile.
"""

from __future__ import annotations

import numpy as np
import pytest

from engine.multimedia.features import TF_LOG, TF_RAW, build_vocabulary

ZONAS = np.array([[0.0, 0.0], [10.0, 10.0], [0.0, 10.0], [10.0, 0.0]], dtype=np.float32)


def _objeto(zonas: list[int], por_zona: int = 12, semilla: int = 0) -> np.ndarray:
    azar = np.random.default_rng(semilla)
    nubes = [ZONAS[z] + 0.2 * azar.standard_normal((por_zona, 2)) for z in zonas]
    return np.concatenate(nubes).astype(np.float32)


def _vocabulario():
    # La zona 0 esta en todos (no distinguira nada); las otras, repartidas.
    dataset = [
        _objeto([0, 1], semilla=1),
        _objeto([0, 1], semilla=2),
        _objeto([0, 2], semilla=3),
        _objeto([0, 3], semilla=4),
    ]
    return build_vocabulary(dataset, k=4, seed=0)


# ---------------------------------------------------------------------------
# Forma del vector
# ---------------------------------------------------------------------------


def test_el_histograma_tiene_la_dimension_del_vocabulario() -> None:
    vocabulario = _vocabulario()
    assert vocabulario.histogram(_objeto([1], semilla=9)).shape == (vocabulario.k,)


def test_objetos_de_distinto_tamano_dan_vectores_de_la_misma_dimension() -> None:
    # Es la razon de ser de todo el pipeline: SIFT da N descriptores y N
    # cambia de un objeto a otro, pero el histograma siempre mide K.
    vocabulario = _vocabulario()
    corto = vocabulario.histogram(_objeto([1], por_zona=3, semilla=9))
    largo = vocabulario.histogram(_objeto([1], por_zona=80, semilla=9))
    assert corto.shape == largo.shape == (vocabulario.k,)


def test_el_histograma_viene_en_float32() -> None:
    # Es lo que espera la columna VECTOR del catalogo (#180).
    assert _vocabulario().histogram(_objeto([1], semilla=9)).dtype == np.float32


def test_el_histograma_sale_normalizado() -> None:
    # Con norma 1, el producto punto vale lo mismo que el coseno y el indice
    # se ahorra calcular dos normas por comparacion (ver vector_metric.hpp).
    #
    # La tolerancia no es pereza: el vector se normaliza en float64 y se
    # guarda en float32, que es lo que pide la columna VECTOR, asi que la
    # norma almacenada vale 1 con la precision de float32 y no mas.
    vocabulario = _vocabulario()
    vector = vocabulario.histogram(_objeto([1, 2], semilla=9))
    assert float(np.linalg.norm(vector)) == pytest.approx(1.0, abs=1e-6)


def test_el_tamano_del_objeto_no_cambia_la_direccion_del_vector() -> None:
    # Dos objetos con las mismas proporciones de palabras describen lo mismo
    # aunque uno tenga el triple de descriptores. Es lo que hace que la
    # similitud del coseno sea la metrica que corresponde a un histograma.
    vocabulario = _vocabulario()
    chico = vocabulario.histogram(_objeto([1, 2], por_zona=10, semilla=9), variant=TF_RAW)
    grande = vocabulario.histogram(_objeto([1, 2], por_zona=30, semilla=9), variant=TF_RAW)
    assert float(chico @ grande) > 0.99


# ---------------------------------------------------------------------------
# Que el peso signifique algo
# ---------------------------------------------------------------------------


def test_la_palabra_que_esta_en_todos_los_objetos_no_aporta() -> None:
    # La zona 0 tiene idf 0: un objeto que solo la use da el vector nulo.
    vocabulario = _vocabulario()
    assert not np.any(vocabulario.histogram(_objeto([0], semilla=9)))


def test_agregar_una_palabra_sin_peso_no_cambia_el_vector() -> None:
    vocabulario = _vocabulario()
    solo = vocabulario.histogram(_objeto([2], semilla=9))
    con_relleno = vocabulario.histogram(_objeto([2, 0], semilla=9))
    assert np.allclose(solo, con_relleno, atol=1e-6)


def test_la_palabra_mas_frecuente_del_objeto_pesa_mas() -> None:
    vocabulario = _vocabulario()
    descriptores = np.concatenate(
        [_objeto([2], por_zona=30, semilla=9), _objeto([3], por_zona=3, semilla=8)]
    )
    vector = vocabulario.histogram(descriptores, variant=TF_RAW)
    palabra_abundante = int(vocabulario.codebook.assign(ZONAS[2][None, :])[0])
    palabra_escasa = int(vocabulario.codebook.assign(ZONAS[3][None, :])[0])
    assert vector[palabra_abundante] > vector[palabra_escasa]


def test_dos_objetos_con_las_mismas_palabras_quedan_cerca() -> None:
    vocabulario = _vocabulario()
    uno = vocabulario.histogram(_objeto([2], semilla=11))
    otro = vocabulario.histogram(_objeto([2], semilla=12))
    assert float(uno @ otro) > 0.99


def test_dos_objetos_con_palabras_distintas_quedan_lejos() -> None:
    vocabulario = _vocabulario()
    uno = vocabulario.histogram(_objeto([2], semilla=11))
    otro = vocabulario.histogram(_objeto([3], semilla=12))
    assert float(uno @ otro) < 0.1


# ---------------------------------------------------------------------------
# Variantes del tf
# ---------------------------------------------------------------------------


def test_la_variante_log_aplana_las_repeticiones() -> None:
    # Con tf crudo, treinta descriptores en una palabra la hacen dominar el
    # vector; con log, no tanto.
    vocabulario = _vocabulario()
    descriptores = np.concatenate(
        [_objeto([2], por_zona=30, semilla=9), _objeto([3], por_zona=3, semilla=8)]
    )
    abundante = int(vocabulario.codebook.assign(ZONAS[2][None, :])[0])
    crudo = vocabulario.histogram(descriptores, variant=TF_RAW)[abundante]
    logaritmico = vocabulario.histogram(descriptores, variant=TF_LOG)[abundante]
    assert logaritmico < crudo


def test_las_dos_variantes_dan_vectores_normalizados() -> None:
    vocabulario = _vocabulario()
    for variante in (TF_LOG, TF_RAW):
        vector = vocabulario.histogram(_objeto([1, 2], semilla=9), variant=variante)
        assert float(np.linalg.norm(vector)) == pytest.approx(1.0, abs=1e-6)
