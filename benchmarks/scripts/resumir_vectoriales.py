"""Resume CSV de #191 sin ejecutar indices ni extraer multimedia."""

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path
from statistics import mean, median


def resumir(raiz):
    salida = []
    for carpeta in sorted(raiz.iterdir()):
        if not (carpeta / "completo.json").exists():
            continue
        for tecnica in ("exacto", "ivf", "hnsw"):
            construccion = carpeta / f"{tecnica}_construccion.json"
            datos = json.loads(construccion.read_text()) if construccion.exists() else {}
            memoria = json.loads((carpeta / f"{tecnica}_memoria.json").read_text())
            with (carpeta / f"{tecnica}_consultas.csv").open() as f:
                grupos = defaultdict(list)
                for fila in csv.DictReader(f):
                    grupos[int(fila["parametro"])].append(fila)
            for parametro, filas in grupos.items():
                replicas = defaultdict(list)
                for fila in filas:
                    replicas[int(fila["repeticion"])].append(float(fila["tiempo_ns"]) / 1e6)
                promedios = [mean(v) for v in replicas.values()]
                salida.append(
                    {
                        "dataset": carpeta.name,
                        "tecnica": tecnica,
                        "parametro": parametro,
                        "consulta_ms": median(promedios),
                        "minimo_ms": min(promedios),
                        "maximo_ms": max(promedios),
                        "recall10": mean(float(f["recall10"]) for f in filas),
                        "recall_empates": mean(float(f["recall_empates"]) for f in filas),
                        "entrenamiento_s": datos.get("entrenamiento_s", ""),
                        "indice_s": datos.get("indice_s", ""),
                        "total_s": datos.get("total_s", ""),
                        "indice_bytes": datos.get("indice_bytes", ""),
                        "rss_construccion": datos.get("rss_max_bytes", ""),
                        "rss_busqueda": memoria["rss_max_bytes"],
                    }
                )
    return salida


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("entrada", type=Path)
    p.add_argument("--salida", type=Path, required=True)
    a = p.parse_args()
    filas = resumir(a.entrada)
    if not filas:
        p.error("no hay corridas completas")
    with a.salida.open("x") as f:
        w = csv.DictWriter(f, fieldnames=list(filas[0]))
        w.writeheader()
        w.writerows(filas)
