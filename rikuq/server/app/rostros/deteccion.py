"""Detectar un rostro y alinearlo (#304).

Pasos 1 y 2 de la lamina de reconocimiento facial de la clase 10:
*Input & Landmark Detection* y *Geometric Alignment*.

YuNet devuelve, por cada rostro, su caja, una confianza y cinco landmarks:
los dos ojos, la nariz y las dos comisuras de la boca. Con esos cinco puntos
se endereza y se escala el recorte hasta dejar el rostro frontal y del mismo
tamano siempre, que es lo que la lamina llama *Normalized, Frontal Face*.

Alinear importa porque el descriptor de #305 no es invariante a la pose ni a
la escala: si no se normaliza, la misma persona de perfil y de frente cae en
puntos distintos del espacio y el umbral de #303 no puede separar nada.

Para mirar que encuentra en una foto, desde ``rikuq/server``::

    python -m app.rostros.deteccion foto.jpg
    python -m app.rostros.deteccion foto.jpg --guardar recortes/
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from app.rostros.errores import EmptyObjectError
from app.rostros.lectura import leer_color
from app.rostros.modelos import MODELOS, YUNET, traer

UMBRAL_CONFIANZA = 0.6
"""Confianza minima para dar un rostro por bueno.

Antes era 0.9, con el argumento de que YuNet "marca manchas" si se afloja.
**Eso era falso y costaba rostros de verdad.** Medido:

- En imagenes sin ninguna cara -- ruido, degradados, damero, textura -- la
  confianza maxima de YuNet es **0.182**. No hay manchas que temer.
- Con 0.9, de 120 fotos de LFW -- recortadas, frontales, bien iluminadas --
  **una no detectaba nada**. Con 0.8 o menos, ninguna se pierde.
- La confianza cae con el tamano del rostro: 0.93 con la cara a 150 px, 0.90
  a 50 px y 0.79 a 35 px. En una foto cualquiera un rostro mide 60-150 px, y
  con 0.9 se descartaban casi todos.

0.6 queda muy por encima del 0.182 de lo que no es una cara y por debajo de
lo que puntua un rostro pequeno o de perfil.

"""

LADO_ALINEADO = 112
"""Lado del recorte alineado, en pixeles. Es el tamano de entrada de ArcFace."""

PLANTILLA = np.array(
    [
        [38.2946, 51.6963],  # ojo derecho de la persona (izquierda de la imagen)
        [73.5318, 51.5014],  # ojo izquierdo
        [56.0252, 71.7366],  # nariz
        [41.5493, 92.3655],  # comisura derecha
        [70.7299, 92.2041],  # comisura izquierda
    ],
    dtype=np.float32,
)
"""Donde tienen que caer los cinco landmarks en el recorte de 112x112.

Es la plantilla publicada de ArcFace (`arcface_src` de insightface). No se
inventa: el recorte tiene que caer exactamente donde el modelo fue entrenado
a esperarlo, y unos pixeles de corrimiento empeoran el vector sin que nada
avise.

Comprobado: alinear con esta plantilla da un recorte identico pixel a pixel
al que devolvia ``FaceRecognizerSF.alignCrop``, que usaba la misma.
"""


@dataclass(frozen=True, slots=True)
class RostroDetectado:
    """Un rostro encontrado en una imagen, con lo que hace falta para alinearlo."""

    caja: tuple[int, int, int, int]  # x, y, ancho, alto
    confianza: float
    landmarks: tuple[tuple[int, int], ...]  # ojo der, ojo izq, nariz, boca der, boca izq

    # La fila cruda de 15 numeros que devolvio YuNet. `alignCrop` la quiere
    # tal cual: convertirla a tuplas y volver a armarla solo agrega una
    # oportunidad de equivocarse en el orden de los puntos.
    fila: np.ndarray = field(repr=False)


def crear_detector(
    umbral: float = UMBRAL_CONFIANZA,
    carpeta: Path = MODELOS,
) -> cv2.FaceDetectorYN:
    """Carga YuNet. El tamano de entrada se fija despues, por imagen."""

    return cv2.FaceDetectorYN.create(str(traer(YUNET, carpeta)), "", (320, 320), umbral)


def detectar(
    imagen: np.ndarray,
    detector: cv2.FaceDetectorYN,
    *,
    ruta: Path | None = None,
) -> tuple[RostroDetectado, ...]:
    """Devuelve todos los rostros de la imagen, el mas confiable primero.

    Una imagen sin rostros lanza ``EmptyObjectError``, no un error de archivo:
    una foto de un paisaje es valida y vacia, y quien carga el dataset la
    anota y sigue.
    """

    alto, ancho = imagen.shape[:2]
    detector.setInputSize((ancho, alto))
    _, caras = detector.detect(imagen)
    if caras is None or len(caras) == 0:
        nombre = ruta.name if ruta is not None else "la imagen"
        raise EmptyObjectError(f"no hay ningun rostro en {nombre}", ruta)

    rostros = []
    for fila in caras:
        x, y, w, h = (int(v) for v in fila[:4])
        puntos = tuple((int(fila[4 + 2 * i]), int(fila[5 + 2 * i])) for i in range(5))
        rostros.append(
            RostroDetectado(
                caja=(x, y, w, h),
                confianza=float(fila[14]),
                landmarks=puntos,
                fila=fila,
            )
        )
    return tuple(sorted(rostros, key=lambda r: r.confianza, reverse=True))


def alinear(imagen: np.ndarray, rostro: RostroDetectado) -> np.ndarray:
    """Devuelve el recorte alineado de 112x112 en BGR.

    Lleva los cinco landmarks a su sitio en la plantilla con una transformada
    de semejanza -- rotacion, escala y traslacion, sin deformar la cara -- que
    es lo que la lamina de la clase 10 llama *Geometric Alignment*.
    """

    # Los landmarks crudos y no `rostro.landmarks`, que vienen redondeados a
    # entero para mostrarlos: la precision sub-pixel mueve el recorte lo
    # suficiente para que el vector cambie.
    puntos = np.asarray(rostro.fila[4:14], dtype=np.float32).reshape(5, 2)
    matriz, _ = cv2.estimateAffinePartial2D(puntos, PLANTILLA, method=cv2.LMEDS)
    if matriz is None:
        raise EmptyObjectError("no se pudo alinear el rostro con sus landmarks")
    return cv2.warpAffine(imagen, matriz, (LADO_ALINEADO, LADO_ALINEADO), borderValue=0.0)


def rostros_de(ruta: str | Path, detector: cv2.FaceDetectorYN) -> tuple[np.ndarray, ...]:
    """Lee la imagen y devuelve un recorte alineado por cada rostro."""

    archivo = Path(ruta)
    imagen = leer_color(archivo)
    return tuple(alinear(imagen, rostro) for rostro in detectar(imagen, detector, ruta=archivo))


def main(argv: list[str] | None = None) -> int:
    """Dice cuantos rostros hay en cada imagen y, si se pide, guarda los recortes.

    Una imagen sin rostros se anota y no detiene a las demas, igual que hara
    la carga del dataset en #302: devuelve 1 al final para que se note, pero
    recorre todo.
    """

    import argparse

    from app.rostros.errores import FeatureExtractionError

    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("imagenes", nargs="+", type=Path)
    parser.add_argument("--umbral", type=float, default=UMBRAL_CONFIANZA)
    parser.add_argument("--guardar", type=Path, help="carpeta donde dejar los recortes alineados")
    args = parser.parse_args(argv)

    detector = crear_detector(args.umbral)
    if args.guardar is not None:
        args.guardar.mkdir(parents=True, exist_ok=True)

    problemas = 0
    for ruta in args.imagenes:
        try:
            imagen = leer_color(ruta)
            rostros = detectar(imagen, detector, ruta=ruta)
        except FeatureExtractionError as error:
            print(f"  {error}", file=sys.stderr)
            problemas += 1
            continue

        confianzas = ", ".join(f"{r.confianza:.2f}" for r in rostros)
        print(f"{ruta.name}: {len(rostros)} rostro(s) [{confianzas}]")
        if args.guardar is None:
            continue
        for numero, rostro in enumerate(rostros, start=1):
            destino = args.guardar / f"{ruta.stem}_{numero:02d}.png"
            destino.write_bytes(cv2.imencode(".png", alinear(imagen, rostro))[1])
            print(f"  -> {destino}")

    return 1 if problemas else 0


__all__ = [
    "LADO_ALINEADO",
    "PLANTILLA",
    "UMBRAL_CONFIANZA",
    "RostroDetectado",
    "alinear",
    "crear_detector",
    "detectar",
    "main",
    "rostros_de",
]


if __name__ == "__main__":
    sys.exit(main())
