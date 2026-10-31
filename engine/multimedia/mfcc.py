"""Coeficientes MFCC de un audio: el primer paso del pipeline de sonido.

Es el equivalente de SIFT para audio. SIFT describe una imagen con un vector
por punto de interes; MFCC describe un audio con un vector por ventana de
tiempo. En los dos casos el objeto da una matriz de N x D con N variable, y
por eso los dos necesitan despues el K-Means (#178) y el histograma (#181)
para llegar al vector de dimension fija que se indexa.

El pipeline, que es el estandar desde Davis y Mermelstein (1980):

1. **Preenfasis**: ``y[n] = x[n] - a*x[n-1]``. La voz y la musica pierden
   energia en las frecuencias altas; esto las realza para que no queden
   enterradas bajo los graves.
2. **Ventaneo**: la senal se corta en ventanas cortas que se solapan. Dentro
   de 25 ms el sonido se puede tratar como estacionario; en un segundo, no.
   Cada ventana se multiplica por una Hamming para que los bordes no metan
   frecuencias falsas al cortar.
3. **Espectro de potencia**: ``|FFT|^2 / N`` por ventana. Cuanta energia hay
   en cada frecuencia.
4. **Banco de filtros mel**: se agrupan las frecuencias en bandas triangulares
   espaciadas en la escala mel, que imita al oido: distinguimos bien entre
   200 y 300 Hz, y casi nada entre 9 000 y 9 100 Hz.
5. **Logaritmo**: el oido percibe la intensidad de forma logaritmica, y
   ademas convierte en suma lo que en la senal es producto.
6. **DCT**: las bandas mel vecinas estan muy correlacionadas. La DCT las
   descorrelaciona y concentra la informacion en los primeros coeficientes,
   asi que quedarse con 13 no pierde casi nada.

Se implementa aqui en vez de usar librosa porque el enunciado dice
"implementa" (seccion 2.4.1), porque el resto del motor -- B+, R-Tree, SPIMI,
BM25 -- tambien esta escrito a mano, y porque librosa arrastra numba,
llvmlite y scikit-learn al CI de todo el equipo. De fuera solo se usa
soundfile, para decodificar el archivo.

Todo lo de este modulo es puro -- no toca el core --, asi que se prueba sin
compilar los bindings.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf

from engine.multimedia.errors import FeatureExtractionError

N_MFCC = 13
"""Coeficientes que se conservan tras la DCT. Trece es el valor clasico: del
14 en adelante describen el detalle fino del timbre, que cambia con el
microfono y con la compresion mas que con el contenido."""

N_MELS = 26
"""Bandas del banco de filtros. El doble de los coeficientes, para que la DCT
tenga de donde descorrelacionar."""

VENTANA_MS = 25.0
"""Duracion de cada ventana. Es el compromiso habitual: lo bastante corta
para que el sonido sea estacionario dentro de ella, lo bastante larga para
que la FFT distinga frecuencias graves."""

SALTO_MS = 10.0
"""Cuanto avanza la ventana. Menor que la ventana a proposito: las ventanas
se solapan para que un sonido que cae justo en un borde no se pierda."""

PREENFASIS = 0.97
"""Coeficiente del filtro de preenfasis."""

PISO_LOG = 1e-10
"""Suelo de la energia antes del logaritmo. Sin el, una ventana en silencio
da ``log(0) = -inf``, y de ahi en adelante toda la matriz queda inservible:
el K-Means de #178 propaga el infinito a su centroide y el histograma de
#181 sale con NaN."""


def hz_to_mel(hz: float | np.ndarray) -> float | np.ndarray:
    """Pasa hercios a la escala mel (formula de O'Shaughnessy)."""

    return 2595.0 * np.log10(1.0 + np.asarray(hz, dtype=np.float64) / 700.0)


def mel_to_hz(mel: float | np.ndarray) -> float | np.ndarray:
    """Inversa de ``hz_to_mel``."""

    return 700.0 * (10.0 ** (np.asarray(mel, dtype=np.float64) / 2595.0) - 1.0)


def load_mono(path: str | Path, *, max_seconds: float | None = None) -> tuple[np.ndarray, int]:
    """Lee el audio en un solo canal y devuelve las muestras y su frecuencia.

    Los canales se promedian: MFCC describe el contenido, y un mismo sonido
    en estereo no deberia dar un vector distinto que en mono.

    ``max_seconds`` acota la duracion analizada, y se aplica al LEER y no
    despues: una cancion de cuatro minutos a 44,1 kHz son diez millones de
    muestras, y el enunciado pide datasets de hasta 100 000 objetos. Leer el
    archivo entero para luego tirar el 95 % seria el cuello de botella de la
    construccion del dataset.
    """

    ruta = Path(path)
    if max_seconds is not None and max_seconds <= 0:
        raise ValueError(f"max_seconds tiene que ser positivo, y es {max_seconds}")
    if not ruta.is_file():
        raise FeatureExtractionError(f"no se puede leer {ruta.name!r}: no es un archivo", ruta)
    if ruta.stat().st_size == 0:
        raise FeatureExtractionError(f"{ruta.name!r} esta vacio", ruta)

    try:
        with sf.SoundFile(str(ruta)) as archivo:
            frecuencia = archivo.samplerate
            cuadros = archivo.frames
            if max_seconds is not None:
                cuadros = min(cuadros, int(max_seconds * frecuencia))
            datos = archivo.read(frames=cuadros, dtype="float32", always_2d=True)
    except sf.LibsndfileError as error:
        raise FeatureExtractionError(
            f"{ruta.name!r} no es un audio que soundfile pueda leer: {error}", ruta
        ) from None

    if datos.size == 0:
        raise FeatureExtractionError(f"{ruta.name!r} no tiene muestras de audio", ruta)
    return datos.mean(axis=1), frecuencia


def mel_filterbank(
    sample_rate: int, n_fft: int, n_mels: int = N_MELS, fmin: float = 0.0,
    fmax: float | None = None,
) -> np.ndarray:
    """Banco de ``n_mels`` filtros triangulares, de ``n_mels`` x ``n_fft//2+1``.

    Los centros se reparten de forma uniforme en la escala mel y recien
    despues se traducen a hercios, que es lo que hace que las bandas graves
    salgan angostas y las agudas anchas. Cada filtro sube linealmente desde
    el centro del anterior hasta el suyo y baja hasta el del siguiente, asi
    que los triangulos se solapan y ninguna frecuencia queda sin cubrir.
    """

    if n_mels < 1:
        raise ValueError(f"n_mels tiene que ser positivo, y es {n_mels}")
    tope = sample_rate / 2.0 if fmax is None else fmax
    if not 0.0 <= fmin < tope:
        raise ValueError(f"el rango de frecuencias [{fmin}, {tope}] no es valido")

    bordes_mel = np.linspace(hz_to_mel(fmin), hz_to_mel(tope), n_mels + 2)
    bordes_hz = mel_to_hz(bordes_mel)
    # La conversion a indices de la FFT se recorta al ultimo bin: con
    # fmax = sample_rate/2 el borde superior cae justo en el limite y un
    # redondeo hacia arriba se saldria del arreglo.
    ultimo = n_fft // 2
    bordes = np.clip(np.floor((n_fft + 1) * bordes_hz / sample_rate), 0, ultimo).astype(int)

    banco = np.zeros((n_mels, ultimo + 1), dtype=np.float64)
    for fila in range(n_mels):
        izquierda, centro, derecha = bordes[fila], bordes[fila + 1], bordes[fila + 2]
        if centro > izquierda:
            subida = np.arange(izquierda, centro)
            banco[fila, subida] = (subida - izquierda) / (centro - izquierda)
        if derecha > centro:
            bajada = np.arange(centro, derecha)
            banco[fila, bajada] = (derecha - bajada) / (derecha - centro)
    return banco


def dct_matrix(n_mfcc: int, n_mels: int) -> np.ndarray:
    """Matriz de la DCT-II ortonormal, de ``n_mfcc`` x ``n_mels``.

    Ortonormal para que la transformacion conserve la energia: dos audios
    que se parecen en el espectro tienen que seguir pareciendose despues de
    la DCT, o la distancia que use #179 dejaria de significar lo mismo.
    """

    if not 1 <= n_mfcc <= n_mels:
        raise ValueError(f"n_mfcc tiene que estar entre 1 y n_mels ({n_mels}), y es {n_mfcc}")
    banda = np.arange(n_mels)
    coeficiente = np.arange(n_mfcc)[:, None]
    matriz = np.cos(np.pi * coeficiente * (2 * banda + 1) / (2 * n_mels))
    matriz *= np.sqrt(2.0 / n_mels)
    matriz[0] *= np.sqrt(0.5)
    return matriz


def frame_signal(signal: np.ndarray, length: int, hop: int) -> np.ndarray:
    """Corta la senal en ventanas solapadas de ``length`` cada ``hop``.

    Las muestras del final que no completan una ventana se descartan: media
    ventana rellenada con ceros daria un espectro que el audio no tiene.
    """

    if length < 1 or hop < 1:
        raise ValueError(f"la ventana ({length}) y el salto ({hop}) tienen que ser positivos")
    if len(signal) < length:
        return np.empty((0, length), dtype=signal.dtype)
    cuantas = 1 + (len(signal) - length) // hop
    todas = np.lib.stride_tricks.sliding_window_view(signal, length)
    return todas[::hop][:cuantas]


def extract_mfcc(
    path: str | Path,
    *,
    n_mfcc: int = N_MFCC,
    frame_ms: float = VENTANA_MS,
    hop_ms: float = SALTO_MS,
    n_mels: int = N_MELS,
    max_seconds: float | None = None,
) -> np.ndarray:
    """Devuelve los MFCC de un audio: una fila por ventana de tiempo.

    La matriz es de N x ``n_mfcc`` en ``float32``, con N dependiente de la
    duracion. Es el gemelo de ``extract_sift``: misma forma de contrato,
    mismo tipo de error y el mismo destino en el pipeline de #178 y #181.

    Un audio mas corto que una sola ventana es un error y no una matriz
    vacia, por lo que pide el criterio 3 de #177: un vector de ceros se
    indexaria igual que cualquier otro y el audio apareceria como "parecido"
    a cosas que no se le parecen.
    """

    if frame_ms <= 0 or hop_ms <= 0:
        raise ValueError(f"la ventana ({frame_ms} ms) y el salto ({hop_ms} ms) tienen que ser > 0")

    ruta = Path(path)
    senal, frecuencia = load_mono(ruta, max_seconds=max_seconds)

    largo = max(1, round(frecuencia * frame_ms / 1000.0))
    salto = max(1, round(frecuencia * hop_ms / 1000.0))

    # Preenfasis. La primera muestra no tiene anterior, asi que se deja como
    # esta en vez de suponerle un cero delante.
    realzada = np.concatenate(([senal[0]], senal[1:] - PREENFASIS * senal[:-1]))

    marcos = frame_signal(realzada, largo, salto)
    if len(marcos) == 0:
        duracion = len(senal) / frecuencia
        raise FeatureExtractionError(
            f"{ruta.name!r} dura {duracion * 1000:.0f} ms y no alcanza para una ventana "
            f"de {frame_ms:.0f} ms",
            ruta,
        )

    # La FFT se hace sobre la potencia de dos que cubre la ventana: es mas
    # rapida y el relleno con ceros solo interpola el espectro, no lo inventa.
    n_fft = 1 << (largo - 1).bit_length()
    espectro = np.abs(np.fft.rfft(marcos * np.hamming(largo), n=n_fft)) ** 2 / n_fft

    energias = espectro @ mel_filterbank(frecuencia, n_fft, n_mels).T
    logaritmos = np.log(np.maximum(energias, PISO_LOG))
    return (logaritmos @ dct_matrix(n_mfcc, n_mels).T).astype(np.float32)


__all__ = [
    "N_MELS",
    "N_MFCC",
    "PISO_LOG",
    "PREENFASIS",
    "SALTO_MS",
    "VENTANA_MS",
    "FeatureExtractionError",
    "dct_matrix",
    "extract_mfcc",
    "frame_signal",
    "hz_to_mel",
    "load_mono",
    "mel_filterbank",
    "mel_to_hz",
]
