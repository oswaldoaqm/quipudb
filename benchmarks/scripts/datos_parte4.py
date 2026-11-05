"""Lectura y validacion de fuentes #191/#192; nunca ejecuta benchmarks."""

import csv
import hashlib
import json
import math
from collections import defaultdict
from statistics import mean, median

from benchmarks.scripts.comparar_metricas import coincidencia
from benchmarks.scripts.resumir_vectoriales import resumir

TAMANOS = (1000, 10000, 100000)
MODALIDADES = ("imagenes", "audio")
TECNICAS = ("exacto", "ivf", "hnsw")


def json_de(p):
    with p.open(encoding="utf-8") as f:
        return json.load(f)


def csv_de(p):
    with p.open(encoding="utf-8", newline="") as f:
        filas = list(csv.DictReader(f))
    if not filas:
        raise ValueError(f"CSV vacio: {p}")
    return filas


def numero(x, minimo=0, maximo=math.inf):
    v = float(x)
    if not math.isfinite(v) or not minimo <= v <= maximo:
        raise ValueError(f"valor fuera de rango: {x}")
    return v


def iguales(a, b):
    if not math.isclose(float(a), float(b), rel_tol=1e-10, abs_tol=1e-12):
        raise ValueError(f"resumen inconsistente: {a} != {b}")


def parametros(n, tecnica):
    nlist = round(math.sqrt(n))
    if tecnica == "exacto":
        return [0]
    if tecnica == "hnsw":
        return [10, 20, 40, 80, 160, 320]
    return sorted({p for p in (1, 2, 4, 8, 16, 32, 64, 128, nlist) if p <= nlist})


def validar_corrida(carpeta, modalidad, n, metrica):
    if json_de(carpeta / "completo.json") != {"estado": "ok"}:
        raise ValueError(f"corrida incompleta: {carpeta}")
    entrada, entorno = json_de(carpeta / "entrada.json"), json_de(carpeta / "entorno.json")
    for clave, esperado in {
        "n": n,
        "modalidad": modalidad,
        "nlist": round(math.sqrt(n)),
        "semilla": 20260906,
    }.items():
        if entrada[clave] != esperado:
            raise ValueError(f"{carpeta}: {clave} incorrecto")
    ids = entrada["ids"]
    if len(ids) != 100 or len(set(ids)) != 100 or not all(1 <= i <= n for i in ids):
        raise ValueError("seleccion de consultas invalida")
    for clave, esperado in {
        "metrica": metrica,
        "k": 10,
        "candidatos": 11,
        "consultas": 100,
        "repeticiones": 5,
        "calentamientos": 1,
        "m": 16,
        "ef_construction": 200,
    }.items():
        if entorno[clave] != esperado:
            raise ValueError(f"{carpeta}: parametro {clave} incorrecto")
    tablas = {}
    for tecnica in TECNICAS:
        rows = csv_de(carpeta / f"{tecnica}_consultas.csv")
        tabla = {}
        for r in rows:
            if r["tecnica"] != tecnica or r["modalidad"] != modalidad or int(r["n"]) != n:
                raise ValueError("mezcla de datasets o tecnicas")
            key = (int(r["parametro"]), int(r["repeticion"]), int(r["consulta_id"]))
            if key in tabla:
                raise ValueError("medicion duplicada")
            numero(r["tiempo_ns"], 1)
            numero(r["recall10"], 0, 1)
            numero(r["recall_empates"], float(r["recall10"]), 1)
            numero(r["resultados"], 0, 10)
            if "top10" in r:
                top = json.loads(r["top10"])
                if len(top) != int(r["resultados"]) or len({tuple(x) for x in top}) != len(top):
                    raise ValueError("Top 10 invalido")
            tabla[key] = r
        esperadas = {(p, rep, i) for p in parametros(n, tecnica) for rep in range(5) for i in ids}
        if tabla.keys() != esperadas:
            raise ValueError("mediciones faltantes o parametros inesperados")
        numero(json_de(carpeta / f"{tecnica}_memoria.json")["rss_max_bytes"], 1)
        if tecnica != "exacto":
            build = json_de(carpeta / f"{tecnica}_construccion.json")
            for campo in (
                "entrenamiento_s",
                "indice_s",
                "total_s",
                "indice_bytes",
                "rss_max_bytes",
            ):
                numero(build[campo], 0 if campo == "entrenamiento_s" else 1e-15)
            iguales(build["total_s"], build["entrenamiento_s"] + build["indice_s"])
        tablas[tecnica] = tabla
    return entrada, tablas


def leer_191(raiz):
    for m in MODALIDADES:
        for n in TAMANOS:
            validar_corrida(raiz / f"{m}_{n}", m, n, "COSINE")
    calculadas = resumir(raiz)
    originales = csv_de(raiz / "resumen.csv")

    def key(r):
        return r["dataset"], r["tecnica"], int(r["parametro"])

    esperado = {key(r): r for r in calculadas}
    if len(originales) != len(esperado) or {key(r) for r in originales} != esperado.keys():
        raise ValueError("resumen #191 incompleto")
    for r in originales:
        for campo, v in esperado[key(r)].items():
            if campo not in ("dataset", "tecnica") and v != "":
                iguales(r[campo], v)
    return calculadas


def leer_192(raiz):
    salida = []
    for m in MODALIDADES:
        for n in TAMANOS:
            carpeta = raiz / f"{m}_{n}"
            ec, c = validar_corrida(carpeta / "COSINE", m, n, "COSINE")
            ee, e = validar_corrida(carpeta / "EUCLIDEAN", m, n, "EUCLIDEAN")
            for clave in ("ids", "vectores_sha256"):
                if ec[clave] != ee[clave]:
                    raise ValueError("entradas distintas entre metricas")
            comparaciones = csv_de(carpeta / "comparacion.csv")
            vistos = set()
            for r in comparaciones:
                t = r["tecnica"]
                key = (int(r["parametro"]), int(r["repeticion"]), int(r["consulta_id"]))
                if (t, key) in vistos:
                    raise ValueError("comparacion duplicada")
                vistos.add((t, key))
                rc, re = c[t][key], e[t][key]
                iguales(r["coseno_ns"], rc["tiempo_ns"])
                iguales(r["euclidiana_ns"], re["tiempo_ns"])
                iguales(r["recall_coseno"], rc["recall10"])
                iguales(r["recall_euclidiana"], re["recall10"])
                comunes = coincidencia(json.loads(rc["top10"]), json.loads(re["top10"]))
                iguales(r["comunes"], comunes)
                iguales(r["porcentaje"], comunes * 10)
            if vistos != {(t, k) for t in TECNICAS for k in c[t]}:
                raise ValueError("comparaciones incompletas")
            resumen = csv_de(carpeta / "resumen.csv")
            keys = {(r["tecnica"], int(r["parametro"])) for r in resumen}
            if len(keys) != len(resumen) or keys != {
                (t, p) for t in TECNICAS for p in parametros(n, t)
            }:
                raise ValueError("resumen #192 incompleto")
            for r in resumen:
                t, p = r["tecnica"], int(r["parametro"])
                for nombre, tabla in (("coseno", c[t]), ("euclidiana", e[t])):
                    replicas = defaultdict(list)
                    rows = [v for k, v in tabla.items() if k[0] == p]
                    for v in rows:
                        replicas[int(v["repeticion"])].append(float(v["tiempo_ns"]) / 1e6)
                    iguales(r[nombre + "_ms"], median(mean(v) for v in replicas.values()))
                    iguales(r["recall_" + nombre], mean(float(v["recall10"]) for v in rows))
                cruces = [
                    v for v in comparaciones if v["tecnica"] == t and int(v["parametro"]) == p
                ]
                iguales(r["comunes"], mean(float(v["comunes"]) for v in cruces))
                iguales(r["porcentaje"], 10 * float(r["comunes"]))
                orden = all(c[t][k]["top10"] == e[t][k]["top10"] for k in c[t] if k[0] == p)
                salida.append({**r, "dataset": f"{m}_{n}", "parametro": p, "orden_identico": orden})
    return salida


def seleccionar(filas, modalidad, n, tecnica):
    candidatos = [
        r
        for r in filas
        if r["dataset"] == f"{modalidad}_{n}"
        and r["tecnica"] == tecnica
        and float(r["recall10"]) >= 0.99
    ]
    if not candidatos:
        raise ValueError("ninguna configuracion alcanza Recall >= 0.99")
    return min(candidatos, key=lambda r: (float(r["consulta_ms"]), int(r["parametro"])))


def huellas(raiz):
    """Registra los CSV y JSON; excluye catalogos binarios y logs."""
    return {
        str(p.relative_to(raiz)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(raiz.rglob("*"))
        if p.is_file() and p.suffix in (".csv", ".json")
    }
