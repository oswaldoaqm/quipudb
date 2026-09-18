"""Ejecuta el banco reproducible: Heap minimo (#38), archivos (#39) o indices (#40)."""

from __future__ import annotations

import argparse
import importlib
from pathlib import Path

if __package__:
    from .banco_pruebas import RESULTADOS, Caso, cargar_dataset, ejecutar_banco
    from .casos_archivos import caso_insercion, casos_archivos
    from .casos_indices import casos_indices
    from .generar_datasets import SALIDA_PREDETERMINADA, TAMANOS
else:
    from banco_pruebas import RESULTADOS, Caso, cargar_dataset, ejecutar_banco
    from casos_archivos import caso_insercion, casos_archivos
    from casos_indices import casos_indices
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
    """Conserva la entrada publica del caso minimo del issue #38."""
    if page_size is not None and page_size <= 0:
        raise ValueError("page_size debe ser positivo")

    return caso_insercion(nativo, "heap", page_size)


def nota_entorno(texto: str) -> tuple[str, str]:
    clave, separador, valor = texto.partition("=")
    if not separador or not clave.strip() or not valor.strip():
        raise argparse.ArgumentTypeError("usa CLAVE=VALOR, por ejemplo compilacion_core=Release")
    return clave.strip(), valor.strip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=("heap", "archivos", "indices"), default="heap")
    parser.add_argument(
        "--consultas", type=int, default=1000, help="busquedas de igualdad por lote"
    )
    parser.add_argument(
        "--consultas-rango", type=int, default=100, help="rangos por lote en indices"
    )
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
    if args.consultas < 1 or (args.suite != "heap" and args.consultas > min(args.tamanos)):
        parser.error("consultas debe estar entre 1 y el menor N seleccionado")
    if args.suite == "indices" and not 1 <= args.consultas_rango <= min(
        n - n // 100 + 1 for n in args.tamanos
    ):
        parser.error("consultas-rango debe estar entre 1 y N - N//100 + 1 para todos los tamanos")
    try:
        datasets = [cargar_dataset(args.datasets / f"alumnos_{n}.csv", n) for n in args.tamanos]
        nativo = cargar_bindings()
        if args.suite == "indices":
            casos = casos_indices(nativo, args.consultas, args.consultas_rango, args.page_size)
        elif args.suite == "archivos":
            casos = casos_archivos(nativo, args.consultas, args.page_size)
        else:
            casos = [caso_insercion_heap(nativo, args.page_size)]
        rutas = ejecutar_banco(
            casos,
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
