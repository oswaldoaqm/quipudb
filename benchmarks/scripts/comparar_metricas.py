"""#192: dos metricas, consultas y parametros identicos; reutiliza el banco #191."""

import argparse
import csv
import json
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from statistics import mean, median

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from benchmarks.scripts.banco_pruebas import escribir_csv


def coincidencia(a, b):
    return len({tuple(r) for r in a} & {tuple(r) for r in b})


def comparar(raiz):
    filas = []
    entradas = [
        json.loads((raiz / m / "entrada.json").read_text()) for m in ("COSINE", "EUCLIDEAN")
    ]
    for clave in ("ids", "n", "nlist", "vectores_sha256"):
        if entradas[0][clave] != entradas[1][clave]:
            raise ValueError("entradas diferentes")
    for tecnica in ("exacto", "ivf", "hnsw"):
        tablas = []
        for metrica in ("COSINE", "EUCLIDEAN"):
            with (raiz / metrica / f"{tecnica}_consultas.csv").open() as f:
                tablas.append(
                    {
                        (r["parametro"], r["repeticion"], r["consulta_id"]): r
                        for r in csv.DictReader(f)
                    }
                )
        if tablas[0].keys() != tablas[1].keys():
            raise ValueError("consultas diferentes")
        for key, c in tablas[0].items():
            e = tablas[1][key]
            comunes = coincidencia(json.loads(c["top10"]), json.loads(e["top10"]))
            filas.append(
                {
                    "tecnica": tecnica,
                    "parametro": key[0],
                    "repeticion": key[1],
                    "consulta_id": key[2],
                    "coseno_ns": c["tiempo_ns"],
                    "euclidiana_ns": e["tiempo_ns"],
                    "recall_coseno": c["recall10"],
                    "recall_euclidiana": e["recall10"],
                    "empates_coseno": c["recall_empates"],
                    "empates_euclidiana": e["recall_empates"],
                    "comunes": comunes,
                    "porcentaje": 10 * comunes,
                }
            )
    escribir_csv(raiz / "comparacion.csv", tuple(filas[0]), filas)
    grupos = defaultdict(list)
    for r in filas:
        grupos[(r["tecnica"], r["parametro"])].append(r)
    resumen = []
    for (tecnica, parametro), rows in grupos.items():
        r = {"tecnica": tecnica, "parametro": parametro}
        for nombre in ("coseno", "euclidiana"):
            replicas = defaultdict(list)
            for row in rows:
                replicas[row["repeticion"]].append(float(row[nombre + "_ns"]) / 1e6)
            r[nombre + "_ms"] = median(mean(v) for v in replicas.values())
            r["recall_" + nombre] = mean(float(row["recall_" + nombre]) for row in rows)
        r["comunes"] = mean(row["comunes"] for row in rows)
        r["porcentaje"] = 10 * r["comunes"]
        resumen.append(r)
    escribir_csv(raiz / "resumen.csv", tuple(resumen[0]), resumen)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("dataset", type=Path)
    p.add_argument("--salida", type=Path, required=True)
    p.add_argument("--consultas", type=int, default=100)
    p.add_argument("--repeticiones", type=int, default=5)
    a = p.parse_args()
    if a.salida.resolve().is_relative_to(a.dataset.resolve()):
        p.error("salida dentro de entrada")
    a.salida.mkdir(parents=True, exist_ok=False)
    script = Path(__file__).with_name("ejecutar_benchmarks_vectoriales.py")
    for m in ("COSINE", "EUCLIDEAN"):
        subprocess.run(
            [
                sys.executable,
                "-B",
                str(script),
                str(a.dataset),
                "--salida",
                str(a.salida / m),
                "--metrica",
                m,
                "--consultas",
                str(a.consultas),
                "--repeticiones",
                str(a.repeticiones),
            ],
            check=True,
        )
    comparar(a.salida)


if __name__ == "__main__":
    main()
