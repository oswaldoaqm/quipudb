"""Evaluacion de Rikuq (#306): como se cuentan los aciertos y los tiempos.

Las mediciones se fabrican: la prueba comprueba el **metodo** -- que cuenta
como acierto, que como falso positivo, y que las dos latencias se reporten
por separado -- sin necesitar ni motor ni modelos.
"""

import pytest

from benchmarks.scripts.evaluar_rikuq import (
    LIMITE_MS,
    Medicion,
    _percentil,
    resumir,
    tabla_resumen,
)


def _m(esperado: str, obtenido: str, ms_motor: float = 1.0, ms_total: float = 16.0) -> Medicion:
    return Medicion(
        ruta="lfw/x/x.jpg",
        esperado=esperado,
        obtenido=obtenido,
        distancia=0.3,
        ms_motor=ms_motor,
        ms_total=ms_total,
    )


# --- que cuenta como que --------------------------------------------------


def test_un_acierto_es_el_nombre_correcto() -> None:
    assert _m("Ana", "Ana").acierto
    assert not _m("Ana", "Beto").acierto
    assert not _m("Ana", "").acierto


def test_un_intruso_es_quien_no_estaba_en_la_base() -> None:
    assert _m("", "").es_intruso
    assert not _m("Ana", "Ana").es_intruso


def test_un_falso_positivo_es_ponerle_nombre_a_un_intruso() -> None:
    """Es el error que el enunciado hace notar: "si esta registrada"."""

    assert _m("", "Ana").falso_positivo
    assert not _m("", "").falso_positivo
    # Equivocarse de persona entre dos que si estan no es un falso positivo.
    assert not _m("Ana", "Beto").falso_positivo


# --- el resumen -------------------------------------------------------------


def test_la_precision_solo_mira_a_quien_esta_en_la_base() -> None:
    """Meter a los intrusos en el denominador inflaria o hundiria el numero."""

    resumen = resumir([_m("Ana", "Ana"), _m("Beto", "Beto"), _m("", ""), _m("", "")])

    assert resumen["consultas"] == 2
    assert resumen["precision"] == pytest.approx(1.0)
    assert resumen["intrusos"] == 2


def test_el_rechazo_y_la_confusion_se_cuentan_aparte() -> None:
    """No cuestan lo mismo: un desconocido se reintenta, un nombre malo no."""

    resumen = resumir([_m("Ana", "Ana"), _m("Beto", ""), _m("Caro", "Ana")])

    assert resumen["aciertos"] == 1
    assert resumen["rechazados"] == 1
    assert resumen["confundidos"] == 1


def test_la_tasa_de_falsos_positivos_sale_sobre_los_intrusos() -> None:
    resumen = resumir([_m("", "Ana"), _m("", ""), _m("", ""), _m("", ""), _m("Ana", "Ana")])

    assert resumen["falsos_positivos"] == 1
    assert resumen["tasa_falsos_positivos"] == pytest.approx(0.25)


def test_sin_intrusos_la_tasa_es_cero_y_no_revienta() -> None:
    assert resumir([_m("Ana", "Ana")])["tasa_falsos_positivos"] == 0.0


def test_sin_consultas_la_precision_es_cero_y_no_revienta() -> None:
    assert resumir([_m("", "")])["precision"] == 0.0


# --- las dos latencias ------------------------------------------------------


def test_el_motor_y_la_vuelta_completa_se_reportan_por_separado() -> None:
    """Mezclarlos esconde cual de las dos cosas hay que optimizar."""

    resumen = resumir([_m("Ana", "Ana", ms_motor=2.0, ms_total=18.0)])

    assert resumen["ms_motor_mediana"] == pytest.approx(2.0)
    assert resumen["ms_total_mediana"] == pytest.approx(18.0)


def test_se_reporta_la_mediana_y_no_el_promedio() -> None:
    """Una pausa del recolector mueve el promedio y no la mediana."""

    lentas = [_m("Ana", "Ana", ms_motor=x) for x in (1.0, 1.0, 1.0, 1.0, 900.0)]

    assert resumir(lentas)["ms_motor_mediana"] == pytest.approx(1.0)


def test_el_p95_mira_la_cola_y_no_el_centro() -> None:
    """Rango mas cercano: con 20 muestras el p95 es la decimonovena."""

    assert _percentil([1.0] * 18 + [50.0, 90.0], 95) == pytest.approx(50.0)
    assert _percentil([1.0] * 19 + [90.0], 95) == pytest.approx(1.0)


def test_el_percentil_de_una_lista_vacia_es_cero() -> None:
    assert _percentil([], 95) == 0.0


def test_el_limite_es_el_que_pide_el_enunciado() -> None:
    """ "optimiza para busquedas en menos de 100 ms"."""

    assert LIMITE_MS == 100.0


# --- lo que se imprime y se escribe -----------------------------------------


def test_el_resumen_dice_si_cumple_los_100_ms() -> None:
    rapido = tabla_resumen(resumir([_m("Ana", "Ana", ms_motor=3.0)]))
    lento = tabla_resumen(resumir([_m("Ana", "Ana", ms_motor=250.0)]))

    assert "cumple los 100 ms: si" in rapido
    assert "cumple los 100 ms: NO" in lento
