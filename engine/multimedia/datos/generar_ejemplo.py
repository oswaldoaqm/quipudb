"""Genera la imagen de ejemplo con la que se prueba SIFT (#176).

La imagen se versiona junto al codigo en vez de generarse en cada prueba por
dos razones: una prueba que fabrica su propia entrada solo comprueba que el
codigo es consistente consigo mismo, y un PNG de dos kilobytes no pesa en el
repositorio.

Se dibujan circulos de radios distintos sobre un fondo claro porque SIFT
detecta extremos de la diferencia de gaussianas, que responde a manchas: un
degradado liso no daria ningun punto clave y un damero daria esquinas
inestables entre escalas. Los radios van variados para que aparezcan puntos
en varios niveles de la piramide.

Uso:

    python engine/multimedia/datos/generar_ejemplo.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

LADO = 96
"""Lado de la imagen en pixeles. Suficiente para que SIFT encuentre puntos sin
que el archivo crezca."""

FONDO = 235
MANCHAS = (
    # (centro x, centro y, radio, gris)
    (24, 22, 11, 30),
    (62, 18, 7, 70),
    (18, 58, 6, 55),
    (47, 48, 15, 15),
    (76, 56, 9, 45),
    (34, 80, 8, 25),
    (70, 82, 5, 90),
)
"""Posiciones fijas y no aleatorias: el archivo generado tiene que ser el
mismo en cualquier maquina para que la prueba sea reproducible."""


def dibujar() -> Image.Image:
    """Devuelve la imagen de ejemplo en escala de grises."""

    imagen = Image.new("L", (LADO, LADO), FONDO)
    lapiz = ImageDraw.Draw(imagen)
    for x, y, radio, gris in MANCHAS:
        lapiz.ellipse((x - radio, y - radio, x + radio, y + radio), fill=gris)
    return imagen


def main() -> None:
    destino = Path(__file__).with_name("ejemplo.png")
    dibujar().save(destino, format="PNG", optimize=True)
    print(f"escrito {destino} ({destino.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
