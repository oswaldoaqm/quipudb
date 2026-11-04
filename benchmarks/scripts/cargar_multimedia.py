"""Carga vectores de #190 con load_media_batch (#187), sin extraer ni crear indices."""

from __future__ import annotations

import argparse
import importlib
import sys
import time
from pathlib import Path

import numpy as np

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from benchmarks.scripts.multimedia_dataset import filas, leer_precalculado


def cargar(raiz: Path, catalogo: Path, *, lote: int = 32) -> int:
    from engine.executor import QueryProcessor
    from engine.executor.media_load import MediaRow

    if not 1 <= lote <= 256:
        raise ValueError("el lote debe estar entre 1 y 256")
    try:
        nativo = importlib.import_module("quipudb_native")
    except ImportError as error:
        raise RuntimeError(
            "faltan los bindings quipudb_native; compile el core actual y agregue "
            "build-py/bindings a PYTHONPATH"
        ) from error
    if not hasattr(nativo.DataType, "VECTOR") or not hasattr(nativo, "source_of"):
        raise RuntimeError("los bindings son antiguos; vuelva a compilar el core actual")
    datos, metadata, vectores = leer_precalculado(raiz)
    if catalogo.exists() and any(catalogo.iterdir()):
        raise ValueError(f"{catalogo} no esta vacio; no se reutilizan catalogos")
    catalogo.mkdir(parents=True, exist_ok=True)
    tabla = datos["modalidad"]
    db = nativo.Database(catalogo / "catalogo.txt")
    processor = QueryProcessor(db, native_module=nativo)
    try:
        processor.execute(
            f"CREATE TABLE {tabla} (id INT PRIMARY KEY, nombre VARCHAR(128), "
            f"ruta VARCHAR(1024), rasgos VECTOR({metadata['k']})) USING HEAP"
        )
        pendientes = []
        insertados = 0

        def escribir():
            reporte = processor.load_media_batch(tabla, pendientes, dimension=metadata["k"])
            if reporte.failed or reporte.inserted != len(pendientes):
                raise RuntimeError(f"carga incompleta: {reporte}; el catalogo conserva filas previas")
            return reporte.inserted

        for identificador, nombre, ruta in filas(raiz, datos, verificar_archivos=False):
            pendientes.append(MediaRow(
                identificador, nombre, str(ruta.resolve()),
                tuple(float(x) for x in vectores[identificador - 1]),
            ))
            if len(pendientes) == lote:
                insertados += escribir()
                pendientes.clear()
        if pendientes:
            insertados += escribir()
        if insertados != datos["cantidad"] or db.table(tabla).size() != insertados:
            raise RuntimeError("el numero de filas cargadas no coincide con el dataset")
        db.flush()
    finally:
        if db.has_table(tabla):
            db.close(tabla)
    processor = None
    db = None

    # Una instancia nueva lee las paginas persistidas. Comparar todas las filas, sin extraer.
    reabierta = nativo.Database(catalogo / "catalogo.txt")
    registros = None
    try:
        if reabierta.table_info(tabla).indexes:
            raise RuntimeError("la carga multimedia no debe crear indices")
        # scan() materializa todos los registros en el binding. source_of conserva
        # solo la fila actual y permite verificar 100k sin una lista de vectores.
        registros = nativo.source_of(reabierta.table(tabla))
        for fila, registro in zip(filas(raiz, datos, verificar_archivos=False), registros, strict=True):
            identificador, nombre, ruta = fila
            if (
                list(registro[:3]) != [identificador, nombre, str(ruta.resolve())]
                or not np.array_equal(np.asarray(registro[3], dtype=np.float32),
                                      vectores[identificador - 1])
            ):
                raise RuntimeError(f"persistencia incorrecta en el objeto {identificador}")
    finally:
        registros = None
        reabierta.close(tabla)
    return insertados


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--catalogo", type=Path, required=True, help="directorio nuevo o vacio")
    parser.add_argument("--lote", type=int, default=32)
    args = parser.parse_args()
    try:
        inicio = time.perf_counter()
        cantidad = cargar(args.dataset.resolve(), args.catalogo.resolve(), lote=args.lote)
        print(f"{cantidad} filas cargadas y verificadas al reabrir; sin indices")
        print(f"validacion, carga y verificacion: {time.perf_counter() - inicio:.3f} s")
    except (ValueError, OSError, ImportError, RuntimeError) as error:
        parser.exit(1, f"error: {error}\n")


if __name__ == "__main__":
    main()
