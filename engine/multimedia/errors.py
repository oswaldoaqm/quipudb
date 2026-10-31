"""El error que comparten todos los extractores de caracteristicas.

Vive en su propio modulo y no junto a uno de los extractores porque cada uno
arrastra su dependencia pesada -- SIFT trae OpenCV, MFCC trae soundfile -- y
tomar el error de uno obligaria a instalar la dependencia del otro. Aqui no
se importa nada fuera de la biblioteca estandar.
"""

from __future__ import annotations

from pathlib import Path


class FeatureExtractionError(ValueError):
    """No se pudieron extraer caracteristicas de un archivo.

    Cubre una ruta que no se puede leer, un archivo que no tiene el formato
    esperado y un objeto del que no sale ningun descriptor. Es un error del
    objeto, no del proceso: quien recorre una carpeta entera lo atrapa, anota
    el archivo y sigue con el siguiente, igual que ``CsvLoadError`` en la
    carga masiva.

    ``path`` es el archivo que fallo, cuando el problema tiene uno.
    """

    def __init__(self, message: str, path: Path | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.path = path


__all__ = ["FeatureExtractionError"]
