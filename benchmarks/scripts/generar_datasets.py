"""Genera los CSV comunes de 1k, 10k y 100k registros (issue #5)."""

from __future__ import annotations

import argparse
import csv
import random
from pathlib import Path

SEMILLA = 20260906
TAMANOS = (1_000, 10_000, 100_000)
CABECERA = ("codigo", "nombre", "promedio")
SALIDA_PREDETERMINADA = Path(__file__).resolve().parents[1] / "datasets"


def generar_dataset(tamano: int, salida: Path) -> Path:
    """Escribe un CSV reproducible y devuelve su ruta; reemplaza ese archivo si existe."""
    if tamano not in TAMANOS:
        raise ValueError(f"tamano no soportado: {tamano}; usa uno de {TAMANOS}")

    # Cada llamada empieza desde el mismo estado, aunque antes se haya generado otro tamano.
    rng = random.Random(SEMILLA)
    registros = [
        (codigo, f"alumno{codigo}", rng.randrange(2001))
        for codigo in range(1, tamano + 1)
    ]
    rng.shuffle(registros)

    salida.mkdir(parents=True, exist_ok=True)
    ruta = salida / f"alumnos_{tamano}.csv"
    with ruta.open("w", encoding="utf-8", newline="") as archivo:
        escritor = csv.writer(archivo, lineterminator="\n")
        escritor.writerow(CABECERA)
        for codigo, nombre, centesimas in registros:
            # Mantener las centesimas enteras fija el texto sin redondeos de punto flotante.
            promedio = f"{centesimas // 100}.{centesimas % 100:02d}"
            escritor.writerow((codigo, nombre, promedio))
    return ruta


def main() -> None:
    """Lee las opciones de consola y genera todos los tamanos solicitados."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--salida",
        type=Path,
        default=SALIDA_PREDETERMINADA,
        help="carpeta de salida (por defecto: benchmarks/datasets junto al script)",
    )
    parser.add_argument(
        "--tamanos",
        nargs="+",
        type=int,
        choices=TAMANOS,
        default=TAMANOS,
        help="tamanos a generar (por defecto: 1000 10000 100000)",
    )
    args = parser.parse_args()
    for tamano in args.tamanos:
        ruta = generar_dataset(tamano, args.salida)
        print(f"{ruta}: {tamano} registros")


if __name__ == "__main__":
    main()
