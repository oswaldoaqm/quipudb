"""Los errores que comparten las etapas del pipeline de rostros.

Son los mismos dos de ``engine/multimedia/errors.py`` y con la misma
distincion. Se repiten aqui en vez de importarse porque el servidor de la
aplicacion no importa el motor: solo habla con el por HTTP (ADR 0010). Este
modulo no importa nada fuera de la biblioteca estandar.
"""

from __future__ import annotations

from pathlib import Path


class FeatureExtractionError(ValueError):
    """No se pudo sacar nada de un archivo.

    Cubre una ruta que no se puede leer y un archivo que no tiene el formato
    esperado. Es un error del objeto, no del proceso: quien recorre un dataset
    entero lo atrapa, anota el archivo y sigue con el siguiente.

    ``path`` es el archivo que fallo, cuando el problema tiene uno.
    """

    def __init__(self, message: str, path: Path | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.path = path


class EmptyObjectError(FeatureExtractionError):
    """La imagen se leyo bien, pero no hay ningun rostro en ella.

    La diferencia con el error de arriba importa: una foto de un paisaje es
    una imagen valida sin rostros, y la carga del dataset la anota y sigue; un
    .jpg a medio descargar es un archivo roto y tiene que notarse como tal. Si
    no se distinguieran, una imagen corrupta entraria al dataset como si fuera
    un paisaje.
    """


__all__ = ["EmptyObjectError", "FeatureExtractionError"]
