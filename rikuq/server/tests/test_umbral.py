"""Elegir el umbral con datos (#303), sin modelos ni dataset.

Los vectores se fabrican: la prueba comprueba el metodo —como se reparte el
dataset, que cuenta como acierto y como se elige— y no la calidad de SFace,
que es lo que mide `python -m app.consulta.umbral` sobre LFW.
"""

import numpy as np
import pytest

from app.consulta.umbral import (
    MINIMO_FOTOS,
    PASOS,
    Punto,
    acotar,
    elegir,
    medir,
    repartir,
    tabla,
)
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


# --- la curva -----------------------------------------------------------------


def test_un_umbral_de_cero_no_reconoce_a_nadie() -> None:
    rostros = _rostros({f"P{i}": 4 for i in range(6)})
    reparto = repartir(rostros, _vectores(rostros))

    punto = medir(reparto, [0.0])[0]

    assert punto.aciertos == 0
    assert punto.rechazos == punto.consultas
    assert punto.falsos_positivos == 0


def test_un_umbral_enorme_acepta_hasta_a_los_intrusos() -> None:
    rostros = _rostros({f"P{i}": 4 for i in range(6)})
    reparto = repartir(rostros, _vectores(rostros))

    punto = medir(reparto, [99.0])[0]

    assert punto.rechazos == 0
    assert punto.falsos_positivos == punto.intrusos
    assert punto.tasa_falsos_positivos == pytest.approx(1.0)


def test_con_vectores_separables_hay_un_umbral_que_acierta_todo() -> None:
    """Si no lo hubiera, el metodo no serviria para elegir nada."""

    rostros = _rostros({f"P{i}": 4 for i in range(8)})
    reparto = repartir(rostros, _vectores(rostros, ruido=0.05))

    curva = medir(reparto)
    mejor = elegir(curva)

    assert mejor.precision == pytest.approx(1.0)
    assert mejor.falsos_positivos == 0


def test_el_balance_castiga_al_umbral_que_acepta_todo() -> None:
    """Un umbral con precision alta y cero rechazo de intrusos no sirve."""

    acepta_todo = Punto(
        99.0, aciertos=10, confusiones=0, rechazos=0, intrusos_rechazados=0, falsos_positivos=10
    )
    equilibrado = Punto(
        0.5, aciertos=9, confusiones=1, rechazos=0, intrusos_rechazados=10, falsos_positivos=0
    )

    assert equilibrado.balance > acepta_todo.balance


def test_ante_un_empate_se_elige_el_umbral_mas_estricto() -> None:
    """Equivocarse de persona es peor que no reconocerla."""

    flojo = Punto(
        0.9, aciertos=10, confusiones=0, rechazos=0, intrusos_rechazados=10, falsos_positivos=0
    )
    estricto = Punto(
        0.4, aciertos=10, confusiones=0, rechazos=0, intrusos_rechazados=10, falsos_positivos=0
    )

    assert elegir([flojo, estricto]).umbral == 0.4


def test_elegir_una_curva_vacia_es_un_error() -> None:
    with pytest.raises(ValueError, match="vacia"):
        elegir([])


def test_el_barrido_llega_hasta_donde_se_acepta_a_cualquiera() -> None:
    """Entre personas distintas la distancia ronda 0.95-1.06."""

    assert max(PASOS) >= 1.1
    assert min(PASOS) <= 0.05


def test_la_tabla_trae_una_fila_por_umbral() -> None:
    rostros = _rostros({f"P{i}": 4 for i in range(4)})
    curva = medir(repartir(rostros, _vectores(rostros)), [0.3, 0.6])

    lineas = tabla(curva).splitlines()

    assert len(lineas) == 3  # cabecera y dos umbrales
    assert "umbral" in lineas[0]
    assert lineas[1].strip().startswith("0.30")
