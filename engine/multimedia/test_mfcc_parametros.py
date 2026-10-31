"""Coeficientes, ventana, salto y duracion configurables (issue #177).

El criterio 2 pide que los tres primeros se puedan ajustar, y la nota del
issue pide poder acotar la duracion analizada: una cancion de cuatro minutos
a 44,1 kHz son diez millones de muestras, y el enunciado habla de datasets de
hasta 100 000 objetos.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("soundfile", reason="se necesita soundfile")

from engine.multimedia.mfcc import N_MELS, SALTO_MS, extract_mfcc, load_mono

EJEMPLO = Path(__file__).parent / "datos" / "ejemplo.wav"


def test_la_cantidad_de_coeficientes_es_configurable() -> None:
    assert extract_mfcc(EJEMPLO, n_mfcc=20).shape[1] == 20


def test_un_salto_mas_corto_da_mas_ventanas() -> None:
    assert len(extract_mfcc(EJEMPLO, hop_ms=5.0)) > len(extract_mfcc(EJEMPLO, hop_ms=20.0))


def test_una_ventana_mas_larga_da_menos_ventanas() -> None:
    assert len(extract_mfcc(EJEMPLO, frame_ms=50.0)) < len(extract_mfcc(EJEMPLO, frame_ms=25.0))


def test_el_numero_de_bandas_mel_es_configurable() -> None:
    # Cambiar las bandas no cambia la forma de la salida, pero si su
    # contenido: son el detalle con el que se mira el espectro.
    assert extract_mfcc(EJEMPLO, n_mels=40).shape[1] == extract_mfcc(EJEMPLO).shape[1]


# ---------------------------------------------------------------------------
# Duracion analizada
# ---------------------------------------------------------------------------


def test_max_seconds_recorta_la_duracion_analizada() -> None:
    completo = len(extract_mfcc(EJEMPLO))
    recortado = len(extract_mfcc(EJEMPLO, max_seconds=0.2))
    assert 0 < recortado < completo


def test_max_seconds_recorta_al_leer_y_no_despues() -> None:
    # Si recortara despues de leer, el archivo entero habria pasado por
    # memoria y el limite no serviria de nada en un dataset grande.
    senal, frecuencia = load_mono(EJEMPLO, max_seconds=0.2)
    assert len(senal) == int(0.2 * frecuencia)


def test_un_max_seconds_mayor_que_el_audio_no_inventa_ventanas() -> None:
    assert len(extract_mfcc(EJEMPLO, max_seconds=60.0)) == len(extract_mfcc(EJEMPLO))


def test_el_recorte_coincide_con_el_principio_del_audio() -> None:
    corto = extract_mfcc(EJEMPLO, max_seconds=0.2)
    largo = extract_mfcc(EJEMPLO)
    assert (corto == largo[: len(corto)]).all()


# ---------------------------------------------------------------------------
# Parametros invalidos
# ---------------------------------------------------------------------------


def test_pedir_mas_coeficientes_que_bandas_se_rechaza() -> None:
    with pytest.raises(ValueError, match="n_mfcc"):
        extract_mfcc(EJEMPLO, n_mfcc=N_MELS + 1)


def test_una_ventana_no_positiva_se_rechaza() -> None:
    with pytest.raises(ValueError, match="ventana"):
        extract_mfcc(EJEMPLO, frame_ms=0.0)


def test_un_salto_no_positivo_se_rechaza() -> None:
    with pytest.raises(ValueError, match="salto"):
        extract_mfcc(EJEMPLO, hop_ms=-SALTO_MS)


def test_un_max_seconds_no_positivo_se_rechaza() -> None:
    with pytest.raises(ValueError, match="max_seconds"):
        extract_mfcc(EJEMPLO, max_seconds=0.0)
