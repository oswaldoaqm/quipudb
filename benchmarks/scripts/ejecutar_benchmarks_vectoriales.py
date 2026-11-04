"""Comparacion nativa #191. Salidas exclusivas; nunca escribe en datasets de entrada."""

from __future__ import annotations

import argparse
import json
import math
import random
import resource
import shutil
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

import numpy as np

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from benchmarks.scripts.banco_pruebas import escribir_csv, registrar_entorno
from benchmarks.scripts.cargar_multimedia import cargar
from benchmarks.scripts.multimedia_dataset import leer_precalculado, sha256
from engine.multimedia.kmeans import build_codebook

SEMILLA = 20260906


def rss():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (
        1 if sys.platform == "darwin" else 1024
    )


def guardar(path, valor):
    with path.open("x") as f:
        json.dump(valor, f, indent=2)


def evaluar(obtenidos, exactos):
    """Pares (RID, distancia), sin self. Empates exactos en la escala nativa double."""
    ids = [r for r, _ in obtenidos]
    if len(ids) != len(set(ids)) or len(ids) > 10:
        raise ValueError("RIDs duplicados o exceso de resultados")
    verdad = {r for r, _ in exactos}
    umbral = exactos[-1][1]
    estrictos = {r for r, d in exactos if d < umbral}
    aciertos = len(set(ids) & estrictos)
    empatados = sum(d == umbral for _, d in obtenidos)
    return len(set(ids) & verdad) / 10, (aciertos + min(10 - len(estrictos), empatados)) / 10


def limpiar(matches, tabla, propio):
    salida = []
    for m in matches:
        row = tabla.read(m.rid)
        if row is None or not math.isfinite(m.distance):
            raise ValueError("RID inexistente o distancia invalida")
        if row[0] != propio:
            salida.append(((m.rid.page, m.rid.slot), m.distance))
    if salida != sorted(salida, key=lambda x: (x[1], x[0])):
        raise ValueError("ranking desordenado")
    return salida[:10]


def etapa(args):
    import quipudb_native as native

    from engine.multimedia import features

    def prohibido(*a, **kw):
        raise RuntimeError("extraccion no permitida en #191")

    with (
        patch.object(features, "extract_features", prohibido),
        patch.object(features, "describe", prohibido),
    ):
        raiz = args.salida
        datos, meta, vectores = leer_precalculado(args.dataset)
        n = datos["cantidad"]
        nombre = datos["modalidad"]
        nlist = min(n, round(math.sqrt(n)))
        if args.etapa == "preparar":
            inicio = time.perf_counter()
            cargar(args.dataset, raiz / "catalogo")
            ids = random.Random(SEMILLA).sample(range(1, n + 1), min(args.consultas, n))
            guardar(
                raiz / "entrada.json",
                {
                    "ids": ids,
                    "n": n,
                    "modalidad": nombre,
                    "nlist": nlist,
                    "vectores_sha256": meta["sha256"]["vectores.npy"],
                    "semilla": SEMILLA,
                    "preparacion_s": time.perf_counter() - inicio,
                },
            )
            return
        entrada = json.loads((raiz / "entrada.json").read_text())
        db = native.Database(raiz / "catalogo/catalogo.txt")
        tabla = db.table(nombre)
        metrica = native.VectorMetric.COSINE
        tecnica = args.tecnica
        if args.etapa == "construir":
            inicio = time.perf_counter()
            entrenamiento = 0.0
            if tecnica == "ivf":
                codebook = build_codebook(vectores, nlist, seed=SEMILLA, max_descriptors=10000)
                codebook.save(raiz / "centroides.npz")
                centros = codebook.centroids.tolist()
                entrenamiento = time.perf_counter() - inicio
                comienzo = time.perf_counter()
                indice = db.create_ivf_index(nombre, tecnica, "rasgos", centros)
            else:
                comienzo = time.perf_counter()
                indice = db.create_hnsw_index(
                    nombre,
                    tecnica,
                    "rasgos",
                    metrica,
                    native.HnswParams(m=16, ef_construction=200, seed=SEMILLA),
                )
            db.flush()
            construccion = time.perf_counter() - comienzo
            if indice.size() != n:
                raise ValueError("indice incompleto")
            info = next(i for i in db.table_info(nombre).indexes if i.name == tecnica)
            guardar(
                raiz / f"{tecnica}_construccion.json",
                {
                    "entrenamiento_s": entrenamiento,
                    "indice_s": construccion,
                    "total_s": entrenamiento + construccion,
                    "rss_max_bytes": rss(),
                    "indice_bytes": (raiz / "catalogo" / info.file).stat().st_size,
                },
            )
        else:
            consultas = [(i, vectores[i - 1].tolist()) for i in entrada["ids"]]
            indice = db.index(nombre, tecnica) if tecnica != "exacto" else None
            parametros = (
                [0]
                if tecnica == "exacto"
                else sorted({p for p in (1, 2, 4, 8, 16, 32, 64, 128, nlist) if p <= nlist})
                if tecnica == "ivf"
                else [10, 20, 40, 80, 160, 320]
            )
            referencia = json.loads((raiz / "exactos.json").read_text()) if indice else None
            filas = []
            exactos = []
            for parametro in parametros:
                for rep in range(-1, args.repeticiones):
                    for qi, (propio, q) in enumerate(consultas):
                        if indice:
                            indice.reset_stats()
                        inicio = time.perf_counter_ns()
                        # 11 candidatos permiten eliminar self y conservar hasta 10 vecinos.
                        resultado = (
                            indice.k_nearest(q, 11, metrica, parametro)
                            if indice
                            else native.scan_k_nearest_vector(tabla, "rasgos", q, 11, metrica)
                        )
                        ns = time.perf_counter_ns() - inicio
                        limpios = limpiar(resultado, tabla, propio)
                        if indice:
                            ref = [(tuple(r), d) for r, d in referencia[qi]]
                            recall, empate = evaluar(limpios, ref)
                            if tecnica == "ivf" and parametro == nlist and limpios != ref:
                                raise ValueError("IVF completo difiere del exacto")
                        else:
                            if len(limpios) != 10:
                                raise ValueError("referencia incompleta")
                            recall = empate = 1.0
                            if rep == -1:
                                exactos.append(limpios)
                        if rep >= 0:
                            filas.append(
                                {
                                    "tecnica": tecnica,
                                    "parametro": parametro,
                                    "repeticion": rep,
                                    "consulta_id": propio,
                                    "tiempo_ns": ns,
                                    "recall10": recall,
                                    "recall_empates": empate,
                                    "resultados": len(limpios),
                                    "n": n,
                                    "modalidad": nombre,
                                }
                            )
            escribir_csv(raiz / f"{tecnica}_consultas.csv", tuple(filas[0]), filas)
            if not indice:
                guardar(raiz / "exactos.json", exactos)
            guardar(raiz / f"{tecnica}_memoria.json", {"rss_max_bytes": rss()})
        db.close(nombre)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("dataset", type=Path)
    p.add_argument("--salida", required=True, type=Path)
    p.add_argument("--consultas", type=int, default=100)
    p.add_argument("--repeticiones", type=int, default=5)
    p.add_argument("--etapa", choices=["preparar", "construir", "buscar"])
    p.add_argument("--tecnica", choices=["ivf", "hnsw", "exacto"], default="exacto")
    p.add_argument("--timeout", type=int, default=900)
    args = p.parse_args()
    if args.consultas < 1 or args.repeticiones < 1:
        p.error("consultas y repeticiones deben ser positivas")
    args.dataset = args.dataset.resolve()
    args.salida = args.salida.resolve()
    if args.salida.is_relative_to(args.dataset) or args.dataset.is_relative_to(args.salida):
        p.error("entrada y salida deben estar separadas")
    if args.etapa:
        etapa(args)
        return
    if args.salida.exists():
        p.error("la salida ya existe")
    if shutil.disk_usage(args.dataset).free < 3 * 2**30:
        p.error("se requieren al menos 3 GiB libres")
    args.salida.mkdir(parents=True)
    guardar(
        args.salida / "entorno.json",
        registrar_entorno()
        | {
            "k": 10,
            "candidatos": 11,
            "metrica": "COSINE",
            "consultas": args.consultas,
            "repeticiones": args.repeticiones,
            "calentamientos": 1,
            "m": 16,
            "ef_construction": 200,
            "muestra_ivf": 10000,
            "max_iteraciones": 100,
            "tolerancia": 1e-4,
            "timeout_etapa_s": args.timeout,
            "exclusiones": "self por id",
            "empates": "igualdad exacta double",
            "script_sha256": sha256(Path(__file__)),
            "numpy": np.__version__,
        },
    )
    secuencia = [
        ("preparar", "exacto"),
        ("buscar", "exacto"),
        ("construir", "ivf"),
        ("buscar", "ivf"),
        ("construir", "hnsw"),
        ("buscar", "hnsw"),
    ]
    for fase, tecnica in secuencia:
        print(f"{fase} {tecnica}", flush=True)
        comando = [
            sys.executable,
            "-B",
            str(Path(__file__).resolve()),
            str(args.dataset),
            "--salida",
            str(args.salida),
            "--etapa",
            fase,
            "--tecnica",
            tecnica,
            "--consultas",
            str(args.consultas),
            "--repeticiones",
            str(args.repeticiones),
        ]
        try:
            with (args.salida / f"{fase}_{tecnica}.log").open("x") as log:
                proceso = subprocess.Popen(comando, stdout=log, stderr=subprocess.STDOUT)
                inicio = time.monotonic()
                while proceso.poll() is None:
                    # ps del sistema mide RSS actual, incluido codigo nativo, desde el padre.
                    uso = subprocess.run(
                        ["ps", "-o", "rss=", "-p", str(proceso.pid)],
                        check=False,
                        capture_output=True,
                        text=True,
                    ).stdout.strip()
                    if (
                        uso and int(uso) * 1024 > 2 * 2**30
                    ) or time.monotonic() - inicio > args.timeout:
                        proceso.kill()
                        proceso.wait()
                        raise RuntimeError("limite de 2 GiB RSS o tiempo por etapa")
                    time.sleep(0.5)
                if proceso.returncode:
                    raise RuntimeError(f"etapa fallida, revisar {log.name}")
        except Exception as error:
            if "proceso" in locals() and proceso.poll() is None:
                proceso.kill()
                proceso.wait()
            guardar(
                args.salida / "pendiente.json",
                {"etapa": fase, "tecnica": tecnica, "motivo": str(error)},
            )
            raise
    guardar(args.salida / "completo.json", {"estado": "ok"})


if __name__ == "__main__":
    main()
