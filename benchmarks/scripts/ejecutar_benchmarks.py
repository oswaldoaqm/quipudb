"""Ejecuta el banco reproducible: Heap (#38), archivos (#39), indices (#40) o espacial (#131)."""

from __future__ import annotations

import argparse
import importlib
from pathlib import Path

if __package__:
    from .banco_pruebas import RESULTADOS, Caso, cargar_dataset, ejecutar_banco
    from .cargar_puntos import leer_puntos
    from .casos_archivos import caso_insercion, casos_archivos
    from .casos_espaciales import CONSULTAS, POBLACION_CENTROS, casos_espaciales
    from .casos_indices import casos_indices
    from .generar_datasets import SALIDA_PREDETERMINADA, TAMANOS
else:
    from banco_pruebas import RESULTADOS, Caso, cargar_dataset, ejecutar_banco
    from cargar_puntos import leer_puntos
    from casos_archivos import caso_insercion, casos_archivos
    from casos_espaciales import CONSULTAS, POBLACION_CENTROS, casos_espaciales
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
    parser.add_argument(
        "--suite", choices=("heap", "archivos", "indices", "espacial"), default="heap"
    )
    parser.add_argument(
        "--consultas",
        type=int,
        help=f"consultas por lote (por defecto 1000; {CONSULTAS} en la suite espacial)",
    )
    parser.add_argument(
        "--sin-cruce",
        action="store_true",
        help="en la suite espacial, omitir los radios grandes que buscan el cruce",
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
    if args.consultas is None:
        args.consultas = CONSULTAS if args.suite == "espacial" else 1000
    if args.calentamientos < 0 or args.repeticiones < 1:
        parser.error("calentamientos debe ser >= 0 y repeticiones debe ser >= 1")
    if args.page_size is not None and args.page_size <= 0:
        parser.error("page-size debe ser positivo")
    if len(set(args.tamanos)) != len(args.tamanos):
        parser.error("no repitas tamanos")
    if len(dict(args.entorno)) != len(args.entorno):
        parser.error("no repitas claves de entorno")
    if args.suite == "espacial" and not 1 <= args.consultas <= POBLACION_CENTROS:
        parser.error(f"en la suite espacial consultas debe estar entre 1 y {POBLACION_CENTROS}")
    if args.consultas < 1 or (args.suite != "heap" and args.consultas > min(args.tamanos)):
        parser.error("consultas debe estar entre 1 y el menor N seleccionado")
    if args.suite == "indices" and not 1 <= args.consultas_rango <= min(
        n - n // 100 + 1 for n in args.tamanos
    ):
        parser.error("consultas-rango debe estar entre 1 y N - N//100 + 1 para todos los tamanos")
    if args.suite == "espacial" and args.page_size is not None:
        parser.error("la suite espacial usa el page-size del binding")
    try:
        if args.suite == "espacial":
            datasets = [leer_puntos(args.datasets / f"puntos_{n}.csv", n) for n in args.tamanos]
        else:
            datasets = [cargar_dataset(args.datasets / f"alumnos_{n}.csv", n) for n in args.tamanos]
        nativo = cargar_bindings()
        if args.suite == "espacial":
            casos = casos_espaciales(nativo, args.consultas, cruce=not args.sin_cruce)
        elif args.suite == "indices":
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
