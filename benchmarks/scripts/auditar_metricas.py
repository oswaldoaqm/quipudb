"""Audita identidad de centroides, consultas y orden del Top 10 de #192."""

import argparse
import csv
import json
from pathlib import Path

import numpy as np


def auditar(raiz):
    salida = {}
    for carpeta in sorted(raiz.iterdir()):
        if not carpeta.is_dir():
            continue
        c, e = carpeta / "COSINE", carpeta / "EUCLIDEAN"
        if not (carpeta / "resumen.csv").exists():
            raise ValueError(f"corrida incompleta: {carpeta}")
        with np.load(c / "centroides.npz") as a, np.load(e / "centroides.npz") as b:
            if not np.array_equal(a["centroids"], b["centroids"]):
                raise ValueError("centroides diferentes")
        entrada_c = json.loads((c / "entrada.json").read_text())
        entrada_e = json.loads((e / "entrada.json").read_text())
        for k in ("ids", "vectores_sha256", "n", "nlist"):
            if entrada_c[k] != entrada_e[k]:
                raise ValueError("entradas diferentes")
        datos = {}
        for tecnica in ("exacto", "ivf", "hnsw"):
            tablas = []
            for d in (c, e):
                with (d / f"{tecnica}_consultas.csv").open() as f:
                    rows = list(csv.DictReader(f))
                t = {
                    (r["parametro"], r["repeticion"], r["consulta_id"]): json.loads(r["top10"])
                    for r in rows
                }
                if len(t) != len(rows):
                    raise ValueError("consultas duplicadas")
                tablas.append(t)
            if tablas[0].keys() != tablas[1].keys():
                raise ValueError("consultas diferentes")
            datos[tecnica] = {
                "mediciones": len(tablas[0]),
                "orden_identico": sum(v == tablas[1][k] for k, v in tablas[0].items()),
            }
        salida[carpeta.name] = datos
    return salida


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("entrada", type=Path)
    p.add_argument("--salida", type=Path, required=True)
    a = p.parse_args()
    resultado = auditar(a.entrada)
    with a.salida.open("x") as f:
        json.dump(resultado, f, indent=2)
