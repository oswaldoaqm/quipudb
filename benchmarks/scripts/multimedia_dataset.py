"""Formato y lectura por filas del dataset multimedia de #190."""

from __future__ import annotations

import csv
import hashlib
import json
import platform
from pathlib import Path

import numpy as np

FORMATO = 1
SEMILLA = 20260906
TAMANOS = (1_000, 10_000, 100_000)
MODALIDADES = ("imagenes", "audio")
CABECERA = ("id", "nombre", "ruta", "sha256")
SALIDA = Path(__file__).resolve().parents[1] / "datasets" / "multimedia"


def sha256(ruta: Path) -> str:
    with ruta.open("rb") as archivo:
        return hashlib.file_digest(archivo, "sha256").hexdigest()


def escribir_json(ruta: Path, datos: dict) -> None:
    ruta.write_text(json.dumps(datos, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def leer_json(ruta: Path) -> dict:
    datos = json.loads(ruta.read_text(encoding="utf-8"))
    if not isinstance(datos, dict) or datos.get("formato") != FORMATO:
        raise ValueError(f"formato multimedia desconocido: {ruta}")
    return datos


def versiones() -> dict:
    return {"python": platform.python_version(), "numpy": np.__version__}


def leer_generacion(raiz: Path) -> dict:
    datos = leer_json(raiz / "generacion.json")
    if datos.get("modalidad") not in MODALIDADES:
        raise ValueError("modalidad desconocida")
    if type(datos.get("cantidad")) is not int or not 1 <= datos["cantidad"] <= max(TAMANOS):
        raise ValueError("cantidad fuera de [1, 100000]")
    if sha256(raiz / "objetos.csv") != datos.get("manifiesto_sha256"):
        raise ValueError("el manifiesto no coincide con su SHA-256")
    return datos


def filas(raiz: Path, datos: dict, *, verificar_archivos: bool = True):
    """Orden de insercion explicito; no depende del recorrido del directorio."""
    extension = ".png" if datos["modalidad"] == "imagenes" else ".wav"
    cantidad = 0
    with (raiz / "objetos.csv").open(encoding="utf-8", newline="") as archivo:
        lector = csv.reader(archivo, strict=True)
        if next(lector, None) != list(CABECERA):
            raise ValueError("cabecera incorrecta en objetos.csv")
        for cantidad, fila in enumerate(lector, 1):
            nombre = f"{cantidad:06d}{extension}"
            relativa = f"archivos/{nombre}"
            if len(fila) != 4 or fila[:3] != [str(cantidad), nombre, relativa]:
                raise ValueError(f"fila {cantidad}: id, nombre o ruta incorrectos")
            ruta = raiz / relativa
            if verificar_archivos and sha256(ruta) != fila[3]:
                raise ValueError(f"SHA-256 incorrecto: {ruta}")
            yield cantidad, nombre, ruta
    if cantidad != datos["cantidad"]:
        raise ValueError(f"se esperaban {datos['cantidad']} objetos y se encontraron {cantidad}")


def validar_vectores(vectores: np.ndarray, cantidad: int, k: int) -> None:
    if vectores.dtype != np.dtype("float32") or vectores.shape != (cantidad, k):
        raise ValueError(f"se esperaba una matriz float32 de {cantidad} x {k}")
    for inicio in range(0, cantidad, 1024):
        bloque = vectores[inicio : inicio + 1024]
        if not np.isfinite(bloque).all():
            raise ValueError("hay vectores con NaN o infinitos")
        normas = np.linalg.norm(bloque, axis=1)
        if np.any(normas == 0):
            raise ValueError("hay vectores nulos; revise los patrones y el vocabulario")
        if not np.allclose(normas, 1.0, rtol=0, atol=1e-5):
            raise ValueError("los vectores no tienen norma L2 igual a 1")


def leer_precalculado(raiz: Path):
    """Valida el paquete completo antes de crear un catalogo; nunca extrae rasgos."""
    from engine.multimedia.features import Vocabulary

    datos = leer_generacion(raiz)
    carpeta = raiz / "caracteristicas"
    metadata = leer_json(carpeta / "metadata.json")
    if metadata.get("generacion_sha256") != sha256(raiz / "generacion.json"):
        raise ValueError("los vectores no corresponden a esta generacion")
    for nombre in ("vectores.npy", "vocabulario.npz"):
        if sha256(carpeta / nombre) != metadata.get("sha256", {}).get(nombre):
            raise ValueError(f"SHA-256 incorrecto: {nombre}")
    vocabulario = Vocabulary.load(carpeta / "vocabulario.npz")
    if (
        metadata.get("k") != vocabulario.k
        or vocabulario.documents != datos["cantidad"]
        or vocabulario.dim != (128 if datos["modalidad"] == "imagenes" else 13)
        or not np.isfinite(vocabulario.codebook.centroids).all()
        or not np.isfinite(vocabulario.idf).all()
    ):
        raise ValueError("vocabulario incompatible con el dataset")
    vectores = np.load(carpeta / "vectores.npy", mmap_mode="r", allow_pickle=False)
    validar_vectores(vectores, datos["cantidad"], vocabulario.k)
    for _, nombre, ruta in filas(raiz, datos):
        if len(nombre.encode("utf-8")) > 128 or len(str(ruta.resolve()).encode("utf-8")) > 1024:
            raise ValueError("nombre o ruta excede el esquema multimedia")
    return datos, metadata, vectores
