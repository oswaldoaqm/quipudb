"""Los modelos ONNX del pipeline de rostros (#304, #305).

Son dos archivos: 230 KB el detector YuNet, cuyo codigo trae OpenCV pero no
sus pesos, y 14 MB el descriptor ArcFace, que se corre con ``cv2.dnn``. No se versionan en git --
un binario de 37 MB no tiene por que vivir en la historia del repositorio --
pero si se versiona su SHA-256, que es lo que permite afirmar que todos
corremos el mismo modelo. Un modelo distinto da vectores distintos, y los
umbrales de #303 dejarian de significar lo que midio #306.

Quedan en ``rikuq/datos/modelos/``.

Para traerlos los dos de una vez, desde ``rikuq/server``::

    python -m app.rostros.modelos
"""

from __future__ import annotations

import hashlib
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx

RIKUQ = Path(__file__).resolve().parents[3]
MODELOS = RIKUQ / "datos" / "modelos"

USER_AGENT = "rikuq/0.1 (proyecto academico, UTEC)"
TROZO = 1 << 20

ZOO = "https://github.com/opencv/opencv_zoo/raw/main/models"

# insightface -- los autores de ArcFace -- publica sus modelos en paquetes
# comprimidos; immich-app espeja el ONNX suelto, que es lo que hace falta.
BUFFALO = "https://huggingface.co/immich-app/buffalo_s/resolve/main"


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

# El descriptor que nombra el enunciado: "extrae embeddings faciales con
# modelos preentrenados (FaceNet, ArcFace)". Es ArcFace sobre MobileFaceNet y
# no sobre ResNet100: la red grande son 261 MB y tarda el triple en CPU, y
# medida contra SFace esta sale en 11,4 ms por rostro contra 13,3 ms.
ARCFACE = Modelo(
    archivo="arcface_w600k_mbf.onnx",
    url=f"{BUFFALO}/recognition/model.onnx",
    sha256="9cc6e4a75f0e2bf0b1aed94578f144d15175f357bdc05e815e5c4a02b319eb4f",
)


def _sha256(ruta: Path) -> str:
    """SHA-256 del archivo, por trozos.

    Se calcula aqui en vez de importarlo de ``app.dataset.lfw`` para no atar
    el cache de modelos al modulo del dataset: son dos cosas independientes y
    la cuenta son cuatro lineas.
    """

    digest = hashlib.sha256()
    with ruta.open("rb") as archivo:
        while trozo := archivo.read(TROZO):
            digest.update(trozo)
    return digest.hexdigest()


def traer(
    modelo: Modelo,
    carpeta: Path = MODELOS,
    log: Callable[[str], None] = lambda _: None,
) -> Path:
    """Devuelve la ruta del modelo, bajandolo la primera vez.

    Si el archivo ya esta y su SHA-256 coincide, no se toca la red. Si esta
    pero no coincide, se vuelve a bajar: lo mas probable es una descarga vieja
    o cortada. Si el archivo recien bajado tampoco coincide, es un error: vale
    mas quedarse sin modelo que correr con uno que no es el medido.
    """

    destino = modelo.ruta(carpeta)
    if destino.is_file():
        if _sha256(destino) == modelo.sha256:
            return destino
        log(f"{destino.name} no coincide con su sha-256, se vuelve a bajar")
        destino.unlink()

    carpeta.mkdir(parents=True, exist_ok=True)
    log(f"bajando {modelo.url}")
    # Se escribe al lado y se renombra despues de comprobar la huella: un
    # archivo a medias nunca llega a ocupar el nombre bueno.
    temporal = destino.with_suffix(".part")
    try:
        with httpx.stream(
            "GET",
            modelo.url,
            follow_redirects=True,
            timeout=120.0,
            headers={"User-Agent": USER_AGENT},
        ) as respuesta:
            respuesta.raise_for_status()
            with temporal.open("wb") as salida:
                for trozo in respuesta.iter_bytes(TROZO):
                    salida.write(trozo)
    except httpx.HTTPError as error:
        temporal.unlink(missing_ok=True)
        raise ModeloError(f"no se pudo bajar {modelo.archivo}: {error}") from error

    huella = _sha256(temporal)
    if huella != modelo.sha256:
        temporal.unlink(missing_ok=True)
        raise ModeloError(
            f"{modelo.archivo} no es el modelo esperado: su sha-256 es {huella} "
            f"y deberia ser {modelo.sha256}"
        )

    temporal.replace(destino)
    log(f"{destino.name} listo")
    return destino


def main(argv: list[str] | None = None) -> int:
    """Trae los dos modelos del proyecto y dice donde quedaron."""

    import argparse

    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--carpeta", type=Path, default=MODELOS)
    args = parser.parse_args(argv)

    try:
        for modelo in (YUNET, ARCFACE):
            ruta = traer(modelo, args.carpeta, log=print)
            print(f"{ruta} ({ruta.stat().st_size} bytes)")
    except ModeloError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


__all__ = ["ARCFACE", "MODELOS", "YUNET", "Modelo", "ModeloError", "main", "traer"]


if __name__ == "__main__":
    sys.exit(main())
