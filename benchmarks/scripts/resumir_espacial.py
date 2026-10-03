"""Convierte una corrida de `--suite espacial` (#131) en las tablas Markdown del informe.

Lee exclusivamente `<id>_resumen.csv` y `<id>_entorno.csv`; no ejecuta
benchmarks ni importa bindings. Imprime las tablas por salida estandar para
pegarlas en `comparacion_rtree_secuencial.md`, de modo que ningun numero del
informe se copie a mano.
"""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from pathlib import Path

if __package__:
    from .banco_pruebas import RESULTADOS
    from .casos_espaciales import RADIOS_CRUCE_KM, RADIOS_KM, VALORES_K
else:
    from banco_pruebas import RESULTADOS
    from casos_espaciales import RADIOS_CRUCE_KM, RADIOS_KM, VALORES_K

TAMANOS = (1_000, 10_000, 100_000)


def leer(identificador: str, carpeta: Path) -> tuple[dict, dict]:
    """(resumen por (caso, N), entorno clave -> valor) de una ejecucion."""
    resumen = {}
    with (carpeta / f"{identificador}_resumen.csv").open(encoding="utf-8") as archivo:
        for fila in csv.DictReader(archivo):
            resumen[(fila["caso"], int(fila["n_registros"]))] = fila
    with (carpeta / f"{identificador}_entorno.csv").open(encoding="utf-8") as archivo:
        entorno = {fila["clave"]: fila["valor"] for fila in csv.DictReader(archivo)}
    if not resumen:
        raise ValueError(f"la ejecucion {identificador} no tiene resumen")
    return resumen, entorno


def por_consulta_ms(fila: dict) -> float:
    return float(fila["mediana_tiempo_ns"]) / int(fila["n_operaciones"]) / 1e6


def paginas_por_consulta(fila: dict) -> float:
    return float(fila["mediana_paginas_leidas"]) / int(fila["n_operaciones"])


def _ms(valor: float) -> str:
    return f"{valor:.3f}" if valor < 10 else f"{valor:.1f}"


def tabla_consultas(resumen, entorno, operaciones, metrica) -> list[str]:
    lineas = [
        (
            "| Consulta | N | Secuencial ms | R-Tree ms | Aceleracion | Pag. sec. | Pag. R-Tree "
            "| Devueltos/consulta |"
        ),
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for operacion in operaciones:
        for n in TAMANOS:
            sec = resumen.get((f"secuencial_{operacion}_{metrica}", n))
            rt = resumen.get((f"rtree_{operacion}_{metrica}", n))
            if sec is None or rt is None:
                continue
            clave = f"caso.rtree_{operacion}_{metrica}.{n}.resultados_totales"
            devueltos = int(entorno[clave]) / int(rt["n_operaciones"]) if clave in entorno else None
            if devueltos is None:  # k-NN: siempre k
                devueltos = float(operacion.split("_")[1])
            lineas.append(
                f"| {operacion} | {n:,} | {_ms(por_consulta_ms(sec))} | "
                f"{_ms(por_consulta_ms(rt))} | "
                f"{por_consulta_ms(sec) / por_consulta_ms(rt):.1f}x | "
                f"{paginas_por_consulta(sec):.0f} | {paginas_por_consulta(rt):.1f} | "
                f"{devueltos:,.0f} |".replace(",", " ")
            )
    return lineas


def tabla_construccion_y_espacio(resumen) -> list[str]:
    lineas = [
        (
            "| N | Construccion R-Tree ms | Pag. leidas | Pag. escritas | Heap KiB | R-Tree KiB "
            "| R-Tree / Heap |"
        ),
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for n in TAMANOS:
        fila = resumen.get(("rtree_construccion_indice", n))
        if fila is None:
            continue
        heap = float(fila["mediana_datos_bytes"])
        rtree = float(fila["mediana_indices_bytes"])
        lineas.append(
            f"| {n:,} | {float(fila['mediana_tiempo_ns']) / 1e6:.1f} | "
            f"{float(fila['mediana_paginas_leidas']):,.0f} | "
            f"{float(fila['mediana_paginas_escritas']):,.0f} | {heap / 1024:,.0f} | "
            f"{rtree / 1024:,.0f} | {rtree / heap:.2f} |".replace(",", " ")
        )
    return lineas


def tabla_metricas(resumen) -> list[str]:
    lineas = [
        "| Consulta | N | Tecnica | Haversine ms | Euclidiana ms | Haversine / Euclidiana |",
        "|---|---:|---|---:|---:|---:|",
    ]
    operaciones = [f"radio_{r}km" for r in RADIOS_KM] + [f"knn_{k}" for k in VALORES_K]
    for operacion in operaciones:
        for tecnica in ("secuencial", "rtree"):
            n = TAMANOS[-1]
            hav = resumen.get((f"{tecnica}_{operacion}_haversine", n))
            euc = resumen.get((f"{tecnica}_{operacion}_euclidiana", n))
            if hav is None or euc is None:
                continue
            lineas.append(
                f"| {operacion} | {n:,} | {tecnica} | {_ms(por_consulta_ms(hav))} | "
                f"{_ms(por_consulta_ms(euc))} | "
                f"{por_consulta_ms(hav) / por_consulta_ms(euc):.2f} |".replace(",", " ")
            )
    return lineas


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("ejecucion", help="identificador <fecha>_<uuid> de la corrida")
    parser.add_argument("--entrada", type=Path, default=RESULTADOS)
    args = parser.parse_args()
    resumen, entorno = leer(args.ejecucion, args.entrada)

    grupos = defaultdict(list)
    radios = [f"radio_{r}km" for r in RADIOS_KM]
    knn = [f"knn_{k}" for k in VALORES_K]
    cruce = [f"radio_{r}km" for r in RADIOS_KM + RADIOS_CRUCE_KM]
    grupos["Radio (Haversine)"] = tabla_consultas(resumen, entorno, radios, "haversine")
    grupos["Radio (euclidiana)"] = tabla_consultas(resumen, entorno, radios, "euclidiana")
    grupos["k-NN (Haversine)"] = tabla_consultas(resumen, entorno, knn, "haversine")
    grupos["k-NN (euclidiana)"] = tabla_consultas(resumen, entorno, knn, "euclidiana")
    grupos["Cruce: radios grandes (Haversine)"] = tabla_consultas(
        resumen, entorno, cruce, "haversine"
    )
    grupos["Construccion y espacio"] = tabla_construccion_y_espacio(resumen)
    grupos["Haversine vs euclidiana (N = 100 000)"] = tabla_metricas(resumen)

    print(f"<!-- generado por resumir_espacial.py desde {args.ejecucion} -->")
    for titulo, lineas in grupos.items():
        print(f"\n#### {titulo}\n")
        print("\n".join(lineas))


if __name__ == "__main__":
    main()
