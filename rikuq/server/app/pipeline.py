"""Adaptador del pipeline existente a bytes; modelos cargados solo al consultar."""

from dataclasses import dataclass
from typing import Protocol

from app.rostros.errores import EmptyObjectError, FeatureExtractionError


class ModeloNoDisponible(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class RostroEntrada:
    caja: tuple[int, int, int, int]
    vector: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class ImagenEntrada:
    ancho: int
    alto: int
    rostros: tuple[RostroEntrada, ...]


class Pipeline(Protocol):
    def extraer(self, datos: bytes) -> ImagenEntrada: ...


class PipelineArcFace:
    def __init__(self) -> None:
        self._detector = None
        self._descriptor = None

    def extraer(self, datos: bytes) -> ImagenEntrada:
        # No importar cv2 al crear el API: pruebas HTTP con un Pipeline falso.
        try:
            import cv2
            import numpy as np

            from app.rostros.deteccion import alinear, crear_detector, detectar
            from app.rostros.embedding import crear_descriptor, describir
            from app.rostros.modelos import ARCFACE, YUNET
        except ImportError as error:
            raise ModeloNoDisponible("instala rikuq/server/requirements.txt") from error

        imagen = cv2.imdecode(np.frombuffer(datos, dtype=np.uint8), cv2.IMREAD_COLOR)
        if imagen is None:
            raise FeatureExtractionError("el archivo no es una imagen valida")
        alto, ancho = imagen.shape[:2]
        if alto * ancho > 16_000_000:
            raise FeatureExtractionError("reduce la imagen a menos de 16 megapixeles")
        if self._detector is None:
            if not YUNET.ruta().is_file() or not ARCFACE.ruta().is_file():
                raise ModeloNoDisponible("descarga los modelos con python -m app.rostros.modelos")
            self._detector, self._descriptor = crear_detector(), crear_descriptor()
        rostros = detectar(imagen, self._detector)
        if len(rostros) > 8:
            raise FeatureExtractionError("se admiten como maximo 8 rostros por imagen")
        entradas = tuple(
            RostroEntrada(
                rostro.caja,
                tuple(float(v) for v in describir(alinear(imagen, rostro), self._descriptor)),
            )
            for rostro in rostros
        )
        if not entradas:
            raise EmptyObjectError("no hay ningun rostro en la imagen")
        return ImagenEntrada(ancho, alto, entradas)
