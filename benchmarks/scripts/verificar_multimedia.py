"""Verifica un dataset multimedia y su catalogo; cuenta vectores distintos en disco."""

from __future__ import annotations

import argparse
import csv
import json
import os
import sqlite3
import sys
import tempfile
import time
from contextlib import closing
from pathlib import Path

import numpy as np

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from benchmarks.scripts.multimedia_dataset import (
    filas,
    leer_precalculado,
    sha256,
)


def archivos_de(carpeta: Path):
    """Recorre nombres sin construir una lista del directorio completo."""
    with os.scandir(carpeta) as entradas:
        for entrada in entradas:
            if entrada.is_dir(follow_symlinks=False):
                yield from archivos_de(Path(entrada.path))
            elif entrada.is_file(follow_symlinks=False):
                yield Path(entrada.path)


def contar_distintos(vectores, manifiesto: Path, *, comprobar=lambda: None) -> dict:
    with (
        tempfile.TemporaryDirectory(prefix="quipudb-unicidad-") as temporal,
        closing(sqlite3.connect(Path(temporal) / "unicos.sqlite")) as sql,
    ):
        sql.execute("PRAGMA cache_size=-4096")
        sql.execute("CREATE TABLE vectores (valor BLOB PRIMARY KEY, cantidad INTEGER) "
                    "WITHOUT ROWID")
        sql.execute("CREATE TABLE archivos (sha TEXT PRIMARY KEY) WITHOUT ROWID")
        for inicio in range(0, len(vectores), 1024):
            sql.executemany(
                "INSERT INTO vectores VALUES (?, 1) ON CONFLICT(valor) "
                "DO UPDATE SET cantidad=cantidad+1",
                ((v.tobytes(),) for v in vectores[inicio : inicio + 1024]),
            )
            sql.commit()
            comprobar()
        with manifiesto.open(encoding="utf-8", newline="") as archivo:
            sql.executemany("INSERT OR IGNORE INTO archivos VALUES (?)",
                            ((r["sha256"],) for r in csv.DictReader(archivo)))
        distintos, grupos, maximo = sql.execute(
            "SELECT COUNT(*), SUM(cantidad>1), MAX(cantidad) FROM vectores"
        ).fetchone()
        originales = sql.execute("SELECT COUNT(*) FROM archivos").fetchone()[0]
    return {"vectores_distintos": distintos, "grupos_repetidos": grupos,
                "max_repeticiones": maximo, "archivos_distintos_sha256": originales}


def verificar(raiz: Path, catalogo: Path, *, consultas: int = 20,
              comprobar=lambda: None) -> dict:
    """Lee todas las filas persistidas; la extraccion solo contrasta la muestra de consultas."""
    if consultas < 0:
        raise ValueError("consultas no puede ser negativo")
    if not (catalogo / "catalogo.txt").is_file():
        raise ValueError("el catalogo no existe; no se crea durante la verificacion")
    import quipudb_native as nativo

    datos, meta, vectores = leer_precalculado(raiz)
    modalidad = datos["modalidad"]
    comprobar()
    db = nativo.Database(catalogo / "catalogo.txt")
    fuente = None
    cantidad = 0
    try:
        if db.table_info(modalidad).indexes:
            raise ValueError("el catalogo de #190 no debe tener indices")
        fuente = nativo.source_of(db.table(modalidad))
        for (i, nombre, ruta), registro in zip(
            filas(raiz, datos, verificar_archivos=False), fuente, strict=True
        ):
            if registro[:3] != [i, nombre, str(ruta.resolve())] or not np.array_equal(
                np.asarray(registro[3], dtype=np.float32), vectores[i - 1]
            ):
                raise ValueError(f"persistencia incorrecta en el objeto {i}")
            cantidad += 1
            if cantidad % 1024 == 0:
                comprobar()
    finally:
        fuente = None
        if db.is_open(modalidad):
            db.close(modalidad)
    if cantidad != datos["cantidad"]:
        raise ValueError("cantidad persistida distinta del dataset")
    distintos = contar_distintos(vectores, raiz / "objetos.csv", comprobar=comprobar)
    seleccion = np.linspace(1, cantidad, min(consultas, cantidad), dtype=int)
    if len(seleccion):
        from engine.multimedia.features import Vocabulary, extract_features

        vocabulario = Vocabulary.load(raiz / "caracteristicas/vocabulario.npz")
        extension = "png" if modalidad == "imagenes" else "wav"
        for i in seleccion:
            esperado = extract_features(raiz / "archivos" / f"{i:06d}.{extension}",
                                        vocabulario, **meta["extraccion"])
            if not np.array_equal(esperado, vectores[i - 1]):
                raise ValueError(f"la extraccion directa difiere en el objeto {i}")
            comprobar()
    tamanos = {}
    asignado = 0
    for nombre, carpeta in (("dataset_bytes", raiz), ("catalogo_bytes", catalogo)):
        total = 0
        for ruta in archivos_de(carpeta):
            stat = ruta.stat()
            total += stat.st_size
            # st_blocks no esta disponible en Windows: no inventar espacio asignado.
            asignado = (asignado + stat.st_blocks * 512
                        if asignado is not None and hasattr(stat, "st_blocks") else None)
        tamanos[nombre] = total
    return dict(modalidad=modalidad, registros_verificados=cantidad, forma=list(vectores.shape),
                dtype=str(vectores.dtype), vectores_nulos=0, no_finitos=0, normalizados=True,
                indices=0, extracciones_directas_verificadas=len(seleccion),
                generacion_sha256=sha256(raiz / "generacion.json"),
                vectores_sha256=meta["sha256"]["vectores.npy"],
                vocabulario_sha256=meta["sha256"]["vocabulario.npz"],
                asignado_bytes=asignado, **tamanos, **distintos)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--catalogo", type=Path, required=True)
    parser.add_argument("--salida", type=Path, help="JSON nuevo; si se omite, solo stdout")
    parser.add_argument("--consultas", type=int, default=20,
                        help="extracciones directas de control; 0 verifica solo lo guardado")
    args = parser.parse_args()
    try:
        if args.salida and args.salida.exists():
            raise ValueError("el informe ya existe; elija otro nombre")
        inicio = time.perf_counter()
        resultado = verificar(args.dataset.resolve(), args.catalogo.resolve(),
                              consultas=args.consultas)
        resultado["segundos"] = time.perf_counter() - inicio
        contenido = json.dumps(resultado, indent=2) + "\n"
        if args.salida:
            args.salida.parent.mkdir(parents=True, exist_ok=True)
            with args.salida.open("x", encoding="utf-8") as archivo:
                archivo.write(contenido)
        print(contenido, end="")
    except (ValueError, OSError, ImportError, RuntimeError) as error:
        parser.exit(1, f"error: {error}\n")


if __name__ == "__main__":
    main()
