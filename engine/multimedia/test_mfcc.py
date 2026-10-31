"""Forma de la matriz MFCC que devuelve la extraccion (issue #177).

No necesitan el core: el modulo es puro sobre archivos. Si soundfile no esta
instalado las pruebas se saltan, para que un companero que todavia no corrio
``pip install -r requirements.txt`` no vea el resto de la suite en rojo. El
CI no puede saltarselas: instala soundfile y comprueba que importa antes de
llamar a pytest.
"""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("soundfile", reason="se necesita soundfile")

from engine.multimedia.mfcc import N_MFCC, SALTO_MS, VENTANA_MS, extract_mfcc, load_mono

EJEMPLO = Path(__file__).parent / "datos" / "ejemplo.wav"


def _wav(destino: Path, muestras: np.ndarray, frecuencia: int = 16_000) -> Path:
    """Escribe un WAV mono de 16 bits con el modulo estandar ``wave``."""

    enteros = np.round(np.clip(muestras, -1.0, 1.0) * 32767).astype("<i2")
    with wave.open(str(destino), "wb") as salida:
        salida.setnchannels(1)
        salida.setsampwidth(2)
        salida.setframerate(frecuencia)
        salida.writeframes(enteros.tobytes())
    return destino


def test_hay_una_fila_por_ventana_de_tiempo() -> None:
    # Es lo que distingue a MFCC de un descriptor global: un audio no da un
    # vector, da una secuencia.
    assert len(extract_mfcc(EJEMPLO)) > 1


def test_cada_fila_tiene_los_coeficientes_pedidos() -> None:
    matriz = extract_mfcc(EJEMPLO)
    assert matriz.ndim == 2
    assert matriz.shape[1] == N_MFCC


def test_el_numero_de_ventanas_sale_de_la_duracion_y_el_salto() -> None:
    senal, frecuencia = load_mono(EJEMPLO)
    largo = round(frecuencia * VENTANA_MS / 1000)
    salto = round(frecuencia * SALTO_MS / 1000)
    assert len(extract_mfcc(EJEMPLO)) == 1 + (len(senal) - largo) // salto


def test_los_coeficientes_vienen_en_float32() -> None:
    # Igual que SIFT: el K-Means de #178 promedia estos valores.
    assert extract_mfcc(EJEMPLO).dtype == np.float32


def test_la_extraccion_es_reproducible() -> None:
    assert np.array_equal(extract_mfcc(EJEMPLO), extract_mfcc(EJEMPLO))


def test_los_dos_tonos_del_ejemplo_dan_vectores_distintos() -> None:
    # El audio son 300 ms de 440 Hz y 300 ms de 1760 Hz. Si los MFCC no
    # distinguieran ambas mitades, no estarian describiendo el contenido.
    matriz = extract_mfcc(EJEMPLO)
    mitad = len(matriz) // 2
    primera, segunda = matriz[: mitad - 5], matriz[mitad + 5 :]
    dentro = np.linalg.norm(primera - primera.mean(axis=0), axis=1).mean()
    entre = np.linalg.norm(primera.mean(axis=0) - segunda.mean(axis=0))
    assert entre > dentro


def test_un_audio_en_silencio_no_da_infinitos(tmp_path: Path) -> None:
    # El log de una energia nula seria -inf, y de ahi en adelante el K-Means
    # de #178 propagaria el infinito a su centroide. Por eso hay un piso.
    ruta = _wav(tmp_path / "silencio.wav", np.zeros(16_000, dtype=np.float32))
    assert np.isfinite(extract_mfcc(ruta)).all()


def test_un_audio_en_estereo_se_promedia_a_un_canal() -> None:
    # Un mismo sonido en estereo no deberia describirse distinto que en mono.
    senal, _ = load_mono(EJEMPLO)
    assert senal.ndim == 1
