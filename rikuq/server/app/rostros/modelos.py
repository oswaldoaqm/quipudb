"""Los modelos ONNX del pipeline de rostros (#304, #305).

OpenCV trae el codigo de YuNet y de SFace, pero no sus pesos. Son dos
archivos: 230 KB el detector y 37 MB el descriptor. No se versionan en git --
un binario de 37 MB no tiene por que vivir en la historia del repositorio --
pero si se versiona su SHA-256, que es lo que permite afirmar que todos
corremos el mismo modelo. Un modelo distinto da vectores distintos, y los
umbrales de #303 dejarian de significar lo que midio #306.

Salen del zoo oficial de OpenCV y quedan en ``rikuq/datos/modelos/``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

RIKUQ = Path(__file__).resolve().parents[3]
MODELOS = RIKUQ / "datos" / "modelos"

ZOO = "https://github.com/opencv/opencv_zoo/raw/main/models"


class ModeloError(RuntimeError):
    """El modelo no se pudo traer, o el archivo no es el que se esperaba."""


@dataclass(frozen=True, slots=True)
class Modelo:
    """Un archivo ONNX identificado por su contenido, no por su nombre."""

    archivo: str
    url: str
    sha256: str

    def ruta(self, carpeta: Path = MODELOS) -> Path:
        return carpeta / self.archivo


# Los SHA-256 se midieron sobre los archivos que bajo este mismo codigo.
YUNET = Modelo(
    archivo="face_detection_yunet_2023mar.onnx",
    url=f"{ZOO}/face_detection_yunet/face_detection_yunet_2023mar.onnx",
    sha256="8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4",
)

SFACE = Modelo(
    archivo="face_recognition_sface_2021dec.onnx",
    url=f"{ZOO}/face_recognition_sface/face_recognition_sface_2021dec.onnx",
    sha256="0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79",
)


__all__ = ["MODELOS", "SFACE", "YUNET", "Modelo", "ModeloError"]
