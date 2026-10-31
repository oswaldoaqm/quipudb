"""Genera el audio de ejemplo con el que se prueba MFCC (#177).

Se versiona junto al codigo por lo mismo que la imagen de SIFT: una prueba
que fabrica su propia entrada solo comprueba que el codigo es consistente
consigo mismo.

El audio son dos tonos puros seguidos, 440 Hz y luego 1760 Hz -- dos octavas
arriba --, porque asi las pruebas pueden comprobar dos cosas distintas: que
la energia cae en la banda mel que corresponde a cada frecuencia, y que los
vectores de la primera mitad se parecen entre si y se diferencian de los de
la segunda. Un ruido blanco no permitiria ni lo uno ni lo otro.

Se escribe con el modulo ``wave`` de la biblioteca estandar y no con
soundfile, para que regenerar el archivo no dependa de la dependencia que el
propio archivo sirve para probar.

Uso:

    python engine/multimedia/datos/generar_audio.py
"""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

FRECUENCIA = 16_000
"""Muestras por segundo. 16 kHz es el estandar de voz y deja el espectro
util por debajo de los 8 kHz, de sobra para 26 bandas mel."""

TONOS_HZ = (440.0, 1760.0)
"""La4 y dos octavas arriba."""

SEGUNDOS_POR_TONO = 0.3
AMPLITUD = 0.6
"""Por debajo de 1 para que al pasar a entero de 16 bits no recorte."""


def sintetizar() -> np.ndarray:
    """Devuelve la senal de los dos tonos, en float de -1 a 1."""

    tramos = []
    for hz in TONOS_HZ:
        t = np.arange(round(FRECUENCIA * SEGUNDOS_POR_TONO)) / FRECUENCIA
        tramos.append(AMPLITUD * np.sin(2 * np.pi * hz * t))
    return np.concatenate(tramos)


def main() -> None:
    destino = Path(__file__).with_name("ejemplo.wav")
    muestras = np.round(sintetizar() * 32767).astype("<i2")
    with wave.open(str(destino), "wb") as salida:
        salida.setnchannels(1)
        salida.setsampwidth(2)
        salida.setframerate(FRECUENCIA)
        salida.writeframes(muestras.tobytes())
    print(f"escrito {destino} ({destino.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
