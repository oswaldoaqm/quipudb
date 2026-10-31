"""La matematica del MFCC: escala mel, banco de filtros, DCT y ventaneo.

Son las piezas que se pueden comprobar sin abrir ningun archivo, y las que
justifican que el pipeline este escrito a mano en vez de delegarlo: si el
banco de filtros estuviera mal, los MFCC saldrian igual de plausibles pero
describirian otra cosa, y eso no se nota mirando la matriz.
"""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("soundfile", reason="se necesita soundfile")

from engine.multimedia.mfcc import (
    N_MELS,
    dct_matrix,
    frame_signal,
    hz_to_mel,
    mel_filterbank,
    mel_to_hz,
)

FRECUENCIA = 16_000
N_FFT = 512


# ---------------------------------------------------------------------------
# Escala mel
# ---------------------------------------------------------------------------


def test_el_cero_de_hercios_es_el_cero_de_mel() -> None:
    assert hz_to_mel(0.0) == 0.0


def test_la_conversion_de_mel_va_y_vuelve() -> None:
    hercios = np.array([0.0, 100.0, 440.0, 1760.0, 8000.0])
    assert np.allclose(mel_to_hz(hz_to_mel(hercios)), hercios)


def test_la_escala_mel_comprime_los_agudos() -> None:
    # Es la razon de ser de toda la escala: 100 Hz de diferencia se oyen
    # enormes en los graves y casi nada en los agudos.
    grave = hz_to_mel(300.0) - hz_to_mel(200.0)
    agudo = hz_to_mel(9100.0) - hz_to_mel(9000.0)
    assert grave > 3 * agudo


# ---------------------------------------------------------------------------
# Banco de filtros
# ---------------------------------------------------------------------------


def test_el_banco_tiene_una_fila_por_banda_y_una_columna_por_bin() -> None:
    assert mel_filterbank(FRECUENCIA, N_FFT, N_MELS).shape == (N_MELS, N_FFT // 2 + 1)


def test_ningun_filtro_es_negativo() -> None:
    # Son pesos de una suma de energias: un peso negativo restaria energia.
    assert (mel_filterbank(FRECUENCIA, N_FFT, N_MELS) >= 0).all()


def test_las_bandas_graves_son_mas_angostas_que_las_agudas() -> None:
    banco = mel_filterbank(FRECUENCIA, N_FFT, N_MELS)
    assert np.count_nonzero(banco[0]) < np.count_nonzero(banco[-1])


def test_cada_filtro_sube_y_baja() -> None:
    # Triangular: un solo maximo. Si el reparto de bordes estuviera mal, un
    # filtro podria salir plano o con dos picos y mezclaria bandas vecinas.
    banco = mel_filterbank(FRECUENCIA, N_FFT, N_MELS)
    for fila in banco:
        activos = np.flatnonzero(fila)
        if len(activos) < 3:
            continue
        pico = int(np.argmax(fila))
        assert np.all(np.diff(fila[activos[0] : pico + 1]) > 0)
        assert np.all(np.diff(fila[pico : activos[-1] + 1]) < 0)


def test_un_tono_puro_activa_la_banda_que_le_corresponde() -> None:
    banco = mel_filterbank(FRECUENCIA, N_FFT, N_MELS)
    ganadoras = []
    for hz in (440.0, 1760.0):
        espectro = np.zeros(N_FFT // 2 + 1)
        espectro[round(hz * N_FFT / FRECUENCIA)] = 1.0
        ganadora = int(np.argmax(banco @ espectro))
        # La banda que mas responde tiene que tener peso en ese bin.
        assert banco[ganadora, round(hz * N_FFT / FRECUENCIA)] > 0
        ganadoras.append(ganadora)
    # Y un tono mas agudo tiene que caer en una banda posterior.
    assert ganadoras[0] < ganadoras[1]


def test_un_banco_sin_bandas_se_rechaza() -> None:
    with pytest.raises(ValueError, match="n_mels"):
        mel_filterbank(FRECUENCIA, N_FFT, 0)


def test_un_rango_de_frecuencias_invertido_se_rechaza() -> None:
    with pytest.raises(ValueError, match="rango de frecuencias"):
        mel_filterbank(FRECUENCIA, N_FFT, N_MELS, fmin=5000.0, fmax=1000.0)


# ---------------------------------------------------------------------------
# DCT
# ---------------------------------------------------------------------------


def test_la_dct_es_ortonormal() -> None:
    # D @ D.T = I es lo que garantiza que conserva la energia: dos audios
    # parecidos en el espectro siguen parecidos despues de la DCT, y la
    # distancia de #179 sigue significando lo mismo.
    matriz = dct_matrix(N_MELS, N_MELS)
    assert np.allclose(matriz @ matriz.T, np.eye(N_MELS))


def test_la_dct_recorta_a_los_coeficientes_pedidos() -> None:
    assert dct_matrix(13, N_MELS).shape == (13, N_MELS)


def test_no_se_pueden_pedir_mas_coeficientes_que_bandas() -> None:
    with pytest.raises(ValueError, match="n_mfcc"):
        dct_matrix(N_MELS + 1, N_MELS)


def test_no_se_pueden_pedir_cero_coeficientes() -> None:
    with pytest.raises(ValueError, match="n_mfcc"):
        dct_matrix(0, N_MELS)


# ---------------------------------------------------------------------------
# Ventaneo
# ---------------------------------------------------------------------------


def test_el_numero_de_ventanas_sale_de_la_longitud_y_el_salto() -> None:
    senal = np.arange(1000, dtype=np.float32)
    assert len(frame_signal(senal, 400, 160)) == 1 + (1000 - 400) // 160


def test_las_ventanas_se_solapan_segun_el_salto() -> None:
    senal = np.arange(10, dtype=np.float32)
    marcos = frame_signal(senal, 4, 2)
    assert marcos[0].tolist() == [0, 1, 2, 3]
    assert marcos[1].tolist() == [2, 3, 4, 5]


def test_una_senal_mas_corta_que_la_ventana_no_da_ninguna() -> None:
    assert len(frame_signal(np.arange(10, dtype=np.float32), 400, 160)) == 0


def test_la_cola_que_no_completa_una_ventana_se_descarta() -> None:
    # Rellenarla con ceros daria un espectro que el audio no tiene.
    assert len(frame_signal(np.arange(9, dtype=np.float32), 4, 2)) == 3


def test_una_ventana_o_un_salto_no_positivos_se_rechazan() -> None:
    with pytest.raises(ValueError, match="salto"):
        frame_signal(np.arange(10, dtype=np.float32), 4, 0)
