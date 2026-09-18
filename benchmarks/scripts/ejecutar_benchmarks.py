"""Ejecuta el banco reproducible con un unico caso: insertar sobre un Heap vacio."""

from __future__ import annotations

import argparse
import importlib
from contextlib import contextmanager
from pathlib import Path

if __package__:
    from .banco_pruebas import RESULTADOS, Caso, Dataset, Operacion, cargar_dataset, ejecutar_banco
    from .generar_datasets import SALIDA_PREDETERMINADA, TAMANOS
else:
    from banco_pruebas import RESULTADOS, Caso, Dataset, Operacion, cargar_dataset, ejecutar_banco
    from generar_datasets import SALIDA_PREDETERMINADA, TAMANOS


def cargar_bindings():
    """La CLI falla explicitamente si no puede medir con el core real."""
    try:
        return importlib.import_module("quipudb_native")
    except ImportError as error:
        raise RuntimeError(
            "no se pueden cargar los bindings quipudb_native; compila con "
            "QUIPUDB_BUILD_PYTHON=ON y configura PYTHONPATH para este Python. "
            f"Detalle: {error}"
        ) from error


def caso_insercion_heap(nativo, page_size: int | None = None) -> Caso:
    """Adaptador minimo al core: no implementa ni simula ninguna estructura."""
    if page_size is not None and page_size <= 0:
        raise ValueError("page_size debe ser positivo")

    @contextmanager
    def preparar(directorio: Path, dataset: Dataset):
        db = nativo.Database(directorio / "catalogo.txt")
        try:
            esquema = nativo.Schema(
                "alumnos",
                [
                    nativo.Column("codigo", nativo.DataType.INT),
                    nativo.Column("nombre", nativo.DataType.VARCHAR, 16),
                    nativo.Column("promedio", nativo.DataType.DOUBLE),
                ],
                key_column=0,
            )
            opciones = {} if page_size is None else {"page_size": page_size}
            tabla = db.create_table(esquema, nativo.kind.HEAP, **opciones)
            info = db.table_info("alumnos")
            archivo_datos = directorio / info.file
            db.flush()

            def insertar():
                for registro in dataset.registros:
                    tabla.insert(registro)
                db.flush()

            def contadores():
                stats = tabla.stats()
                return int(stats.pages_read), int(stats.pages_written)

            def validar():
                # El scan ocurre despues de copiar los contadores de la medicion.
                filas = tuple(tuple(fila) for fila in tabla.scan())
                if tabla.size() != len(dataset.registros) or sorted(filas) != sorted(
                    dataset.registros
                ):
                    raise RuntimeError("el Heap no contiene exactamente los registros insertados")

            yield Operacion(
                n_operaciones=len(dataset.registros),
                ejecutar=insertar,
                reiniciar_contadores=tabla.reset_stats,
                capturar_contadores=contadores,
                medir_espacio=lambda: (archivo_datos.stat().st_size, 0),
                validar=validar,
                configuracion={
                    "page_size": info.page_size,
                    "page_size_origen": "binding" if page_size is None else "explicito",
                    "flush": "incluido_al_final_de_la_insercion",
                    "columna_clave": "codigo",
                    "modulo_nativo": nativo.__file__,
                },
            )
        finally:
            db.close("alumnos")

    return Caso("heap_insercion", "heap", "insercion", preparar)


def nota_entorno(texto: str) -> tuple[str, str]:
    clave, separador, valor = texto.partition("=")
    if not separador or not clave.strip() or not valor.strip():
        raise argparse.ArgumentTypeError("usa CLAVE=VALOR, por ejemplo compilacion_core=Release")
    return clave.strip(), valor.strip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--datasets", type=Path, default=SALIDA_PREDETERMINADA)
    parser.add_argument("--salida", type=Path, default=RESULTADOS)
    parser.add_argument(
        "--temporales", type=Path, help="carpeta existente; por defecto la del sistema"
    )
    parser.add_argument("--tamanos", type=int, nargs="+", choices=TAMANOS, default=TAMANOS)
    parser.add_argument("--calentamientos", type=int, default=1)
    parser.add_argument("--repeticiones", type=int, default=5)
    parser.add_argument("--page-size", type=int, help="si se omite, se usa el valor del binding")
    parser.add_argument(
        "--entorno", type=nota_entorno, action="append", default=[], metavar="CLAVE=VALOR"
    )
    args = parser.parse_args()
    if args.calentamientos < 0 or args.repeticiones < 1:
        parser.error("calentamientos debe ser >= 0 y repeticiones debe ser >= 1")
    if args.page_size is not None and args.page_size <= 0:
        parser.error("page-size debe ser positivo")
    if len(set(args.tamanos)) != len(args.tamanos):
        parser.error("no repitas tamanos")
    if len(dict(args.entorno)) != len(args.entorno):
        parser.error("no repitas claves de entorno")
    try:
        datasets = [cargar_dataset(args.datasets / f"alumnos_{n}.csv", n) for n in args.tamanos]
        caso = caso_insercion_heap(cargar_bindings(), args.page_size)
        rutas = ejecutar_banco(
            [caso],
            datasets,
            salida=args.salida,
            temporales=args.temporales,
            calentamientos=args.calentamientos,
            repeticiones=args.repeticiones,
            notas=dict(args.entorno),
        )
    except (OSError, ValueError, RuntimeError) as error:
        parser.exit(1, f"error: {error}\n")
    for tipo, ruta in rutas.items():
        print(f"{tipo}: {ruta}")


if __name__ == "__main__":
    main()
