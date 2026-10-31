"""Formatos, canales y frecuencias de muestreo que tiene que aguantar (#177).

El WAV de ejemplo es mono y a 16 kHz, asi que por si solo no prueba nada de
esto. Los audios reales llegan en MP3 estereo a 44,1 kHz -- el propio
enunciado usa ``SIMILAR_TO('audio_query.mp3', k=5)`` en la seccion 2.4.3 --
y es ahi donde se rompen las suposiciones.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

sf = pytest.importorskip("soundfile", reason="se necesita soundfile")

from engine.multimedia.mfcc import N_MFCC, extract_mfcc, load_mono

EJEMPLO = Path(__file__).parent / "datos" / "ejemplo.wav"


def _tono(frecuencia: int = 16_000, segundos: float = 0.4, hz: float = 440.0) -> np.ndarray:
    t = np.arange(round(frecuencia * segundos)) / frecuencia
    return (0.6 * np.sin(2 * np.pi * hz * t)).astype(np.float32)


# ---------------------------------------------------------------------------
# Formatos
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("extension", [".wav", ".flac", ".ogg", ".mp3"])
def test_los_formatos_habituales_se_pueden_leer(tmp_path: Path, extension: str) -> None:
    # El MP3 es el del ejemplo del enunciado. libsndfile lo soporta desde la
    # 1.1; si la version instalada no lo trajera, esta prueba lo diria en vez
    # de descubrirlo con el dataset ya armado.
    if extension[1:].upper() not in sf.available_formats():
        pytest.skip(f"libsndfile {sf.__libsndfile_version__} no trae {extension}")
    ruta = tmp_path / f"tono{extension}"
    sf.write(ruta, _tono(), 16_000)
    assert extract_mfcc(ruta).shape[1] == N_MFCC


# ---------------------------------------------------------------------------
# Canales
# ---------------------------------------------------------------------------


def test_un_estereo_se_promedia_a_un_solo_canal(tmp_path: Path) -> None:
    ruta = tmp_path / "estereo.wav"
    sf.write(ruta, np.stack([_tono(), _tono()], axis=1), 16_000)
    muestras, _ = load_mono(ruta)
    assert muestras.ndim == 1


def test_un_estereo_de_canales_iguales_da_lo_mismo_que_el_mono(tmp_path: Path) -> None:
    # Un mismo sonido en estereo no deberia describirse distinto que en mono,
    # o el mismo audio entraria dos veces al indice como si fueran dos cosas.
    mono, estereo = tmp_path / "mono.wav", tmp_path / "estereo.wav"
    senal = _tono()
    sf.write(mono, senal, 16_000)
    sf.write(estereo, np.stack([senal, senal], axis=1), 16_000)
    assert np.allclose(extract_mfcc(mono), extract_mfcc(estereo), atol=1e-4)


def test_los_canales_se_promedian_y_no_se_concatenan(tmp_path: Path) -> None:
    # Dos canales opuestos se cancelan al promediar. Si en vez de promediar
    # se concatenaran o se tomara solo el primero, esto no daria silencio.
    ruta = tmp_path / "opuestos.wav"
    senal = _tono()
    sf.write(ruta, np.stack([senal, -senal], axis=1), 16_000)
    muestras, _ = load_mono(ruta)
    assert np.abs(muestras).max() < 1e-4


# ---------------------------------------------------------------------------
# Frecuencia de muestreo
# ---------------------------------------------------------------------------


def test_la_ventana_se_mide_en_tiempo_y_no_en_muestras(tmp_path: Path) -> None:
    # Dos audios de la misma duracion y distinta frecuencia tienen que dar
    # el mismo numero de ventanas: la ventana son 25 ms, no 400 muestras.
    corto, largo = tmp_path / "8k.wav", tmp_path / "44k.wav"
    sf.write(corto, _tono(8_000, 0.5), 8_000)
    sf.write(largo, _tono(44_100, 0.5), 44_100)
    assert abs(len(extract_mfcc(corto)) - len(extract_mfcc(largo))) <= 1


def test_la_frecuencia_del_archivo_se_respeta() -> None:
    _, frecuencia = load_mono(EJEMPLO)
    assert frecuencia == 16_000
