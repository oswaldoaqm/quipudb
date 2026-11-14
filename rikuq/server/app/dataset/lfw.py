"""El dataset de rostros de Rikuq: LFW mas las fotos del equipo (#301).

Las imagenes no se versionan: LFW se vuelve a bajar y las fotos del equipo son
datos biometricos que no salen de nuestras maquinas. Lo que si se versiona es
el manifiesto, una fila por imagen con su persona, su ruta y su SHA-256. Con
el se comprueba que todos tenemos exactamente los mismos archivos sin tener
que subirlos.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

RIKUQ = Path(__file__).resolve().parents[3]
DATOS = RIKUQ / "datos"

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
