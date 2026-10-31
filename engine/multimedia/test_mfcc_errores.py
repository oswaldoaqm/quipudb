"""Archivos de los que no se pueden sacar coeficientes MFCC (issue #177).

El criterio 3 pide las mismas dos cosas que en SIFT: que el error sea claro
y que no tumbe el proceso. Lo segundo es lo que permite ingerir una carpeta
de miles de audios y terminar con un informe de los que fallaron, en vez de
cortar en el primero.
"""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("soundfile", reason="se necesita soundfile")

from engine.multimedia.errors import FeatureExtractionError
from engine.multimedia.mfcc import extract_mfcc

EJEMPLO = Path(__file__).parent / "datos" / "ejemplo.wav"


def _wav(destino: Path, muestras: np.ndarray, frecuencia: int = 16_000) -> Path:
    enteros = np.round(np.clip(muestras, -1.0, 1.0) * 32767).astype("<i2")
    with wave.open(str(destino), "wb") as salida:
        salida.setnchannels(1)
        salida.setsampwidth(2)
        salida.setframerate(frecuencia)
        salida.writeframes(enteros.tobytes())
    return destino


def test_un_archivo_que_no_es_audio_da_un_error_claro(tmp_path: Path) -> None:
    ruta = tmp_path / "notas.txt"
    ruta.write_text("esto no es un audio", encoding="utf-8")
    with pytest.raises(FeatureExtractionError, match="no es un audio"):
        extract_mfcc(ruta)


def test_un_archivo_vacio_da_un_error_claro(tmp_path: Path) -> None:
    ruta = tmp_path / "vacio.wav"
    ruta.write_bytes(b"")
    with pytest.raises(FeatureExtractionError, match="esta vacio"):
        extract_mfcc(ruta)


def test_una_ruta_que_no_existe_da_un_error_claro(tmp_path: Path) -> None:
    with pytest.raises(FeatureExtractionError, match="no se puede leer"):
        extract_mfcc(tmp_path / "fantasma.wav")


def test_una_carpeta_en_vez_de_un_archivo_da_un_error_claro(tmp_path: Path) -> None:
    carpeta = tmp_path / "audios"
    carpeta.mkdir()
    with pytest.raises(FeatureExtractionError, match="no se puede leer"):
        extract_mfcc(carpeta)


def test_un_wav_corrupto_da_un_error_claro(tmp_path: Path) -> None:
    # Cabecera RIFF valida y el resto basura: es una descarga a medias, y no
    # lo cubre la prueba del archivo que no es audio porque aqui el
    # decodificador si reconoce el formato y falla despues.
    ruta = tmp_path / "corrupto.wav"
    ruta.write_bytes(EJEMPLO.read_bytes()[:20] + b"\x00" * 100)
    with pytest.raises(FeatureExtractionError):
        extract_mfcc(ruta)


def test_un_audio_mas_corto_que_una_ventana_da_un_error_claro(tmp_path: Path) -> None:
    # 10 ms a 16 kHz son 160 muestras y la ventana pide 400.
    ruta = _wav(tmp_path / "corto.wav", np.zeros(160, dtype=np.float32))
    with pytest.raises(FeatureExtractionError, match="no alcanza para una ventana"):
        extract_mfcc(ruta)


def test_el_error_dice_que_archivo_fallo(tmp_path: Path) -> None:
    ruta = _wav(tmp_path / "corto.wav", np.zeros(160, dtype=np.float32))
    with pytest.raises(FeatureExtractionError) as error:
        extract_mfcc(ruta)
    assert error.value.path == ruta


def test_un_audio_malo_no_tumba_el_proceso(tmp_path: Path) -> None:
    corto = _wav(tmp_path / "corto.wav", np.zeros(160, dtype=np.float32))
    extraidos, fallidos = [], []
    for ruta in (corto, EJEMPLO):
        try:
            extraidos.append(extract_mfcc(ruta))
        except FeatureExtractionError as fallo:
            fallidos.append(fallo.path)
    assert len(extraidos) == 1
    assert fallidos == [corto]
