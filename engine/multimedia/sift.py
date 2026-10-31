"""Descriptores SIFT de una imagen: el primer paso del pipeline de imagenes.

SIFT (Lowe, 2004) detecta regiones de interes invariantes a escala y rotacion
y describe cada una con un vector de 128 valores. Una imagen no produce un
vector sino una matriz de N x 128, y N cambia de una imagen a otra: por eso
hacen falta despues la cuantizacion con K-Means (#178) y el histograma de
palabras (#181) para llegar al vector de dimension fija que se indexa.

Que N sea variable es tambien lo que impide comparar dos imagenes restando
sus descriptores: no tienen la misma forma. El codebook es lo que las lleva a
un espacio comun.

Todo lo de este modulo es puro -- no toca el core --, asi que se prueba sin
compilar los bindings.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import cv2
import numpy as np

DESCRIPTOR_LENGTH = 128
"""Valores que tiene un descriptor SIFT. Lo fija el algoritmo: una rejilla de
4x4 celdas alrededor del punto clave, con un histograma de 8 orientaciones en
cada una."""

SIFT_DESDE = "4.4"
"""Primera version de OpenCV que trae SIFT en el paquete principal. Estuvo
anos solo en opencv-contrib (xfeatures2d) por la patente de Lowe, que vencio
en marzo de 2020."""


class FeatureExtractionError(ValueError):
    """No se pudieron extraer descriptores de un archivo.

    Cubre una ruta que no se puede leer, un archivo que no es una imagen y una
    imagen sin puntos de interes. Es un error del objeto, no del proceso:
    quien recorre una carpeta entera lo atrapa, anota el archivo y sigue con
    el siguiente, igual que ``CsvLoadError`` en la carga masiva.

    ``path`` es el archivo que fallo, cuando el problema tiene uno.
    """

    def __init__(self, message: str, path: Path | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.path = path


def read_grayscale(path: str | Path) -> np.ndarray:
    """Lee la imagen en escala de grises y la devuelve como matriz de bytes.

    SIFT trabaja sobre intensidades, no sobre color: el detector busca
    extremos de la diferencia de gaussianas, que se define sobre un solo
    canal.

    No se usa ``cv2.imread`` con la ruta a proposito. En Windows devuelve
    ``None`` sin avisar cuando la ruta tiene tildes, y ese ``None`` es
    indistinguible del que devuelve ante un archivo que no es una imagen: el
    mensaje de error acabaria culpando al archivo equivocado. Leyendo los
    bytes con ``pathlib`` la ruta la resuelve Python, y a ``imdecode`` solo le
    queda decidir si el contenido es una imagen.
    """

    ruta = Path(path)
    try:
        datos = ruta.read_bytes()
    except OSError as error:
        motivo = error.strerror or type(error).__name__
        raise FeatureExtractionError(f"no se puede leer {ruta.name!r}: {motivo}", ruta) from None
    if not datos:
        raise FeatureExtractionError(f"{ruta.name!r} esta vacio", ruta)

    imagen = cv2.imdecode(np.frombuffer(datos, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
    if imagen is None:
        raise FeatureExtractionError(
            f"{ruta.name!r} no es una imagen que OpenCV pueda decodificar", ruta
        )
    return imagen


def create_detector(max_keypoints: int | None = None) -> cv2.SIFT:
    """Crea el detector SIFT, acotado a ``max_keypoints`` puntos si se pide.

    OpenCV entiende ``nfeatures=0`` como "sin limite", y cuando hay limite se
    queda con los mejores por contraste y no con los primeros que encuentra,
    asi que recortar no sesga la imagen hacia una esquina.

    Pero ese limite es BLANDO: ``retainBest`` conserva todos los puntos que
    empatan con la respuesta del ultimo que entra. Quien necesite un tope
    exacto usa ``extract_sift``, que recorta encima de esto.
    """

    if not hasattr(cv2, "SIFT_create"):
        raise FeatureExtractionError(
            f"la version de OpenCV instalada ({cv2.__version__}) no trae SIFT; "
            f"se necesita opencv-python-headless {SIFT_DESDE} o posterior"
        )
    if max_keypoints is not None and max_keypoints < 1:
        raise ValueError(f"max_keypoints tiene que ser positivo, y es {max_keypoints}")
    return cv2.SIFT_create(nfeatures=0 if max_keypoints is None else max_keypoints)


def extract_sift(path: str | Path, *, max_keypoints: int | None = None) -> np.ndarray:
    """Devuelve los descriptores SIFT de una imagen: una fila por punto clave.

    La matriz es de N x 128 en ``float32``, con N variable. ``max_keypoints``
    acota el costo: una foto grande da miles de descriptores, y sobre un
    dataset de 100 000 objetos eso vuelve inviable el K-Means de #178. El
    tope es exacto -- el de OpenCV no lo es, ver ``_mejores``.

    Una imagen sin puntos de interes es un error y no una matriz vacia, por lo
    que pide el criterio 3 de #176: un vector de ceros se indexaria igual que
    cualquier otro y la imagen apareceria como "parecida" a cosas que no se le
    parecen. Quien construye el dataset decide que hacer con ese archivo;
    ``ExtractFeatures`` (#181) es quien define el vector para ese caso.
    """

    ruta = Path(path)
    imagen = read_grayscale(ruta)
    detector = create_detector(max_keypoints)
    puntos, descriptores = detector.detectAndCompute(imagen, None)

    # detectAndCompute devuelve None, no una matriz vacia, cuando no encontro
    # ningun punto clave -- una imagen de un solo color es el caso tipico. Sin
    # esta rama el llamador revienta con AttributeError al pedir .shape, que
    # es exactamente el "tumbar el proceso" que el issue prohibe.
    if descriptores is None or len(descriptores) == 0:
        raise FeatureExtractionError(
            f"{ruta.name!r} no tiene puntos de interes: SIFT no encontro ninguno", ruta
        )
    if max_keypoints is not None and len(descriptores) > max_keypoints:
        descriptores = _mejores(puntos, descriptores, max_keypoints)
    return descriptores


def _mejores(
    puntos: Sequence[cv2.KeyPoint], descriptores: np.ndarray, cuantos: int
) -> np.ndarray:
    """Deja los ``cuantos`` descriptores de mayor respuesta.

    Hace falta porque el ``nfeatures`` de OpenCV es un tope blando:
    ``KeyPointsFilter::retainBest`` conserva TODOS los puntos que empatan con
    la respuesta del ultimo que entra, y SIFT emite un punto por cada
    orientacion dominante de una misma ubicacion, todos con la misma
    respuesta. Los empates son la norma y no la excepcion: en la imagen de
    ejemplo, pedir 3 puntos devuelve 5 descriptores. Quien dimensiono su
    presupuesto de memoria con ese numero se queda corto sin enterarse.

    El orden del desempate es estable para que dos extracciones de la misma
    imagen den la misma matriz: el codebook de #178 se construye una vez y
    las consultas tienen que cuantizarse contra ese mismo.
    """

    respuestas = np.fromiter(
        (-punto.response for punto in puntos), dtype=np.float32, count=len(puntos)
    )
    return descriptores[np.argsort(respuestas, kind="stable")[:cuantos]]


__all__ = [
    "DESCRIPTOR_LENGTH",
    "SIFT_DESDE",
    "FeatureExtractionError",
    "create_detector",
    "extract_sift",
    "read_grayscale",
]
