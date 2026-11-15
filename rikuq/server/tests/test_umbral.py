"""Elegir el umbral con datos (#303), sin modelos ni dataset.

Los vectores se fabrican: la prueba comprueba el metodo —como se reparte el
dataset, que cuenta como acierto y como se elige— y no la calidad de SFace,
que es lo que mide `python -m app.consulta.umbral` sobre LFW.
"""

import numpy as np
import pytest

from app.consulta.umbral import MINIMO_FOTOS, acotar, repartir
from app.dataset.lfw import Rostro


def _unitario(semilla: int) -> np.ndarray:
    """Un vector normalizado distinto por semilla, pero estable."""

    rng = np.random.default_rng(semilla)
    crudo = rng.normal(size=8).astype(np.float32)
    return crudo / np.linalg.norm(crudo)


def _cercano(base: np.ndarray, ruido: float, semilla: int) -> np.ndarray:
    """Un vector parecido a `base`: simula otra foto de la misma persona."""

    rng = np.random.default_rng(semilla)
    movido = base + ruido * rng.normal(size=base.shape).astype(np.float32)
    return movido / np.linalg.norm(movido)


def _rostros(personas: dict[str, int]) -> list[Rostro]:
    return [
        Rostro(persona, f"lfw/{persona}/{persona}_{i:04d}.jpg", "abc", "lfw")
        for persona, cuantas in personas.items()
        for i in range(1, cuantas + 1)
    ]


def _vectores(rostros: list[Rostro], ruido: float = 0.15) -> dict[str, np.ndarray]:
    """Fotos de la misma persona cerca entre si, personas distintas lejos."""

    centros: dict[str, np.ndarray] = {}
    sacados: dict[str, np.ndarray] = {}
    for numero, rostro in enumerate(rostros):
        if rostro.persona not in centros:
            centros[rostro.persona] = _unitario(len(centros) * 101 + 7)
        sacados[rostro.ruta] = _cercano(centros[rostro.persona], ruido, numero)
    return sacados


# --- acotar -------------------------------------------------------------------


def test_acotar_descarta_a_quien_tiene_pocas_fotos() -> None:
    """Hacen falta dos para la base y una para consultar."""

    rostros = _rostros({"Ana": 5, "Beto": 1, "Caro": 3})

    acotados = acotar(rostros, minimo=3)

    assert {r.persona for r in acotados} == {"Ana", "Caro"}


def test_acotar_se_queda_con_las_primeras_identidades() -> None:
    """Filtrar antes de sacar vectores ahorra minutos sobre 4324 imagenes."""

    rostros = _rostros({"Ana": 4, "Beto": 4, "Caro": 4})

    acotados = acotar(rostros, identidades=2, minimo=3)

    assert {r.persona for r in acotados} == {"Ana", "Beto"}


def test_el_minimo_por_defecto_son_tres_fotos() -> None:
    assert MINIMO_FOTOS == 3


# --- repartir -----------------------------------------------------------------


def test_una_foto_no_esta_en_la_base_y_en_las_consultas() -> None:
    """Si lo estuviera, la distancia seria 0 y todo pareceria funcionar."""

    rostros = _rostros({f"P{i}": 4 for i in range(6)})
    reparto = repartir(rostros, _vectores(rostros), proporcion_intrusos=0.0)

    for persona, vector in reparto.consultas:
        for guardado in reparto.base[persona]:
            assert not np.array_equal(vector, guardado)


def test_los_intrusos_no_entran_a_la_base() -> None:
    """Son los que tienen que salir desconocidos."""

    rostros = _rostros({f"P{i}": 4 for i in range(10)})
    reparto = repartir(rostros, _vectores(rostros), proporcion_intrusos=0.3)

    nombres_intrusos = {persona for persona, _ in reparto.intrusos}
    assert nombres_intrusos
    assert nombres_intrusos.isdisjoint(reparto.base)


def test_repartir_necesita_al_menos_dos_identidades() -> None:
    rostros = _rostros({"Ana": 4})

    with pytest.raises(ValueError, match="al menos 2 identidades"):
        repartir(rostros, _vectores(rostros))


def test_repartir_salta_las_imagenes_sin_vector() -> None:
    """Las que YuNet no pudo leer no deben contar como fotos disponibles."""

    rostros = _rostros({f"P{i}": 4 for i in range(4)})
    vectores = _vectores(rostros)
    del vectores[rostros[0].ruta]

    reparto = repartir(rostros, vectores, proporcion_intrusos=0.0, minimo=3)

    guardados = sum(len(v) for v in reparto.base.values()) + len(reparto.consultas)
    assert guardados == len(rostros) - 1
    assert reparto.intrusos == []
