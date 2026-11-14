"""El dataset de rostros de Rikuq: LFW mas las fotos del equipo (#301).

Las imagenes no se versionan: LFW se vuelve a bajar y las fotos del equipo son
datos biometricos que no salen de nuestras maquinas. Lo que si se versiona es
el manifiesto, una fila por imagen con su persona, su ruta y su SHA-256. Con
el se comprueba que todos tenemos exactamente los mismos archivos sin tener
que subirlos.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

RIKUQ = Path(__file__).resolve().parents[3]
DATOS = RIKUQ / "datos"

# Carpetas dentro de DATOS. El origen tambien es la primera parte de la ruta en
# el manifiesto, asi que leerlo no necesita adivinar de donde salio cada imagen.
LFW = "lfw"
EQUIPO = "equipo"

EXTENSIONES = (".jpg", ".jpeg", ".png")

# Un trozo grande calcula el SHA-256 sin cargar el archivo entero en memoria.
TROZO = 1 << 20


class DatasetError(RuntimeError):
    """La descarga o el manifiesto no se pudieron completar."""


@dataclass(frozen=True, slots=True)
class Rostro:
    """Una imagen del dataset, tal como la guarda el manifiesto."""

    persona: str
    ruta: str  # relativa a la carpeta de datos, siempre con "/"
    sha256: str
    origen: str

    def archivo(self, datos: Path = DATOS) -> Path:
        return datos / Path(self.ruta)


def sha256(ruta: Path) -> str:
    """SHA-256 del archivo, leido por trozos."""

    digest = hashlib.sha256()
    with ruta.open("rb") as archivo:
        while trozo := archivo.read(TROZO):
            digest.update(trozo)
    return digest.hexdigest()


def recolectar(raiz: Path, origen: str) -> Iterator[Rostro]:
    """Recorre ``raiz/<persona>/<imagen>`` y devuelve un Rostro por imagen.

    El nombre de la carpeta es el nombre de la persona, que es como vienen
    tanto LFW como las fotos del equipo. Una carpeta que no existe no es un
    error: el equipo puede no haber puesto sus fotos todavia.
    """

    if not raiz.is_dir():
        return
    for carpeta in sorted(p for p in raiz.iterdir() if p.is_dir()):
        for imagen in sorted(carpeta.iterdir()):
            if imagen.suffix.lower() not in EXTENSIONES:
                continue
            yield Rostro(
                persona=carpeta.name,
                ruta=f"{origen}/{carpeta.name}/{imagen.name}",
                sha256=sha256(imagen),
                origen=origen,
            )
