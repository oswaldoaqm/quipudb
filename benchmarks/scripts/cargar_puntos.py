"""Carga un CSV de puntos (issue #130) en una tabla Heap de QuipuDB con indice R-Tree.

La lectura y validacion del CSV no necesita los bindings; la carga si, y falla
explicitamente si no estan compilados. Las comparaciones del 2.2.4 (#131, #132)
reutilizan `leer_puntos` para que las tres tecnicas reciban exactamente las
mismas filas en el mismo orden.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib
import io
import math
import time
from dataclasses import dataclass
from pathlib import Path

if __package__:
    from .generar_puntos import CABECERA, TAMANOS
else:
    from generar_puntos import CABECERA, TAMANOS

TABLA = "puntos"
INDICE = "puntos_ubicacion"
LARGO_CIUDAD = 16


@dataclass(frozen=True)
class DatasetPuntos:
    ruta: Path
    sha256: str
    # (id, ciudad, latitud, longitud), en el orden del CSV.
    registros: tuple[tuple[int, str, float, float], ...]


def leer_puntos(ruta: Path, tamano: int | None = None) -> DatasetPuntos:
    """Lee, convierte y valida todo el CSV antes de cargar o medir nada."""
    if tamano is not None and tamano not in TAMANOS:
        raise ValueError(f"tamano no soportado: {tamano}; usa uno de {TAMANOS}")
    try:
        contenido = ruta.read_bytes()
    except FileNotFoundError as error:
        raise ValueError(
            f"dataset inexistente: {ruta}; ejecuta benchmarks/scripts/generar_puntos.py"
        ) from error

    filas = []
    try:
        lector = csv.reader(io.StringIO(contenido.decode("utf-8"), newline=""), strict=True)
        if next(lector, None) != list(CABECERA):
            raise ValueError("la cabecera debe ser " + ",".join(CABECERA))
        for numero, fila in enumerate(lector, start=2):
            if len(fila) != len(CABECERA):
                raise ValueError(f"fila {numero}: se requieren {len(CABECERA)} columnas")
            try:
                identificador, latitud, longitud = int(fila[0]), float(fila[2]), float(fila[3])
            except ValueError as error:
                raise ValueError(f"fila {numero}: id o coordenadas no numericos") from error
            # Los ids son 1..N en orden: comprobarlo asi tambien descarta duplicados.
            if identificador != numero - 1:
                raise ValueError(f"fila {numero}: se esperaba el id {numero - 1}")
            ciudad = fila[1]
            if not ciudad or len(ciudad.encode("utf-8")) > LARGO_CIUDAD or "\0" in ciudad:
                raise ValueError(f"fila {numero}: ciudad incompatible con VARCHAR(16)")
            # isfinite descarta NaN e infinitos, que harian falsa cualquier comparacion.
            if not (math.isfinite(latitud) and -90.0 <= latitud <= 90.0):
                raise ValueError(f"fila {numero}: latitud fuera de [-90, 90]")
            if not (math.isfinite(longitud) and -180.0 <= longitud <= 180.0):
                raise ValueError(f"fila {numero}: longitud fuera de [-180, 180]")
            filas.append((identificador, ciudad, latitud, longitud))
        if tamano is not None and len(filas) != tamano:
            raise ValueError(f"se esperaban {tamano} puntos, se encontraron {len(filas)}")
    except (ValueError, UnicodeError, csv.Error) as error:
        raise ValueError(f"dataset invalido {ruta}: {error}") from error
    return DatasetPuntos(ruta.resolve(), hashlib.sha256(contenido).hexdigest(), tuple(filas))


def importar_bindings():
    """El modulo nativo, o un error que explica como obtenerlo."""
    try:
        return importlib.import_module("quipudb_native")
    except ImportError as error:
        raise RuntimeError(
            "no se pueden cargar los bindings quipudb_native; compila con "
            "-DQUIPUDB_BUILD_PYTHON=ON y agrega build-py/bindings a PYTHONPATH"
        ) from error


def esquema_puntos(nativo):
    """id INT clave, ciudad VARCHAR(16), ubicacion POINT."""
    return nativo.Schema(
        TABLA,
        [
            nativo.Column("id", nativo.DataType.INT),
            nativo.Column("ciudad", nativo.DataType.VARCHAR, LARGO_CIUDAD),
            nativo.Column("ubicacion", nativo.DataType.POINT),
        ],
        key_column=0,
    )


def cargar_quipudb(nativo, db, dataset: DatasetPuntos, *, con_indice: bool = True):
    """Crea la tabla Heap, inserta las filas en el orden del CSV y, si se pide,
    construye el R-Tree sobre `ubicacion`. Devuelve (tabla, indice o None).

    El R-Tree solo se puede montar sobre un Heap, cuyos RID son estables. Se
    construye despues de cargar, con `create_index`, que recorre la tabla: es la
    misma operacion que mide el 2.2.4 como tiempo de construccion del indice.
    """
    tabla = db.create_table(esquema_puntos(nativo), nativo.kind.HEAP)
    for identificador, ciudad, latitud, longitud in dataset.registros:
        tabla.insert([identificador, ciudad, nativo.GeoPoint(latitud, longitud)])
    indice = None
    if con_indice:
        indice = db.create_index(TABLA, INDICE, "ubicacion", nativo.kind.RTREE)
    db.flush()
    return tabla, indice


def main() -> None:
    """Carga un CSV en un catalogo nuevo e informa cantidades, tiempos y tamanos."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("dataset", type=Path, help="CSV generado por generar_puntos.py")
    parser.add_argument(
        "--catalogo",
        type=Path,
        required=True,
        help="carpeta nueva o vacia donde se crean el catalogo y los archivos",
    )
    parser.add_argument("--sin-indice", action="store_true", help="no construir el R-Tree")
    args = parser.parse_args()

    dataset = leer_puntos(args.dataset)
    args.catalogo.mkdir(parents=True, exist_ok=True)
    if any(args.catalogo.iterdir()):
        parser.error(f"{args.catalogo} no esta vacia; no se reutilizan catalogos")
    nativo = importar_bindings()
    db = nativo.Database(args.catalogo / "catalogo.txt")

    inicio = time.perf_counter()
    tabla, indice = cargar_quipudb(nativo, db, dataset, con_indice=not args.sin_indice)
    segundos = time.perf_counter() - inicio

    print(f"{dataset.ruta}: {len(dataset.registros)} puntos, sha256 {dataset.sha256}")
    print(f"tabla {TABLA}: {len(tabla)} filas")
    if indice is not None:
        print(f"indice {INDICE} (rtree): {len(indice)} entradas")
    print(f"carga completa en {segundos:.3f} s")
    for archivo in sorted(args.catalogo.iterdir()):
        print(f"  {archivo.name}: {archivo.stat().st_size} bytes")


if __name__ == "__main__":
    main()
