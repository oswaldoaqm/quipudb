"""Casos #194: resultados persistidos y consultas nativas sobre copias temporales."""

import hashlib
import json
import math
import random
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

import numpy as np

from benchmarks.scripts.datos_parte4 import validar_corrida
from benchmarks.scripts.ejecutar_benchmarks_vectoriales import limpiar
from benchmarks.scripts.multimedia_dataset import filas, leer_precalculado, sha256

SEMILLA = 20260906
K = 5
PARAMETROS = {"exacto": 0, "ivf": 16, "hnsw": 80}


def huella_resultados(caso):
    serializado = json.dumps(caso["resultados"], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serializado.encode("utf-8")).hexdigest()


def seleccionar(tablas, semilla=SEMILLA):
    """Dos menores, mediana y mayor solapamiento Top 5, desempate aleatorio fijo."""
    ids = sorted({key[2] for key in tablas["exacto"]})
    random.Random(semilla).shuffle(ids)

    def calidad(q):
        ref = {tuple(x) for x in json.loads(tablas["exacto"][(0, 0, q)]["top10"])[:K]}
        return sum(
            len(ref & {tuple(x) for x in json.loads(tablas[t][(p, 0, q)]["top10"])[:K]})
            for t, p in PARAMETROS.items()
            if t != "exacto"
        )

    ids.sort(key=calidad)
    if len(ids) < 4:
        raise ValueError("se requieren al menos cuatro consultas")
    return [ids[0], ids[1], ids[len(ids) // 2], ids[-1]]


def validar_resultados(resultados, consulta, k=K):
    if len(resultados) != k:
        raise ValueError("resultados faltantes")
    ids = [r["id"] for r in resultados]
    rids = [tuple(r["rid"]) for r in resultados]
    if len(set(ids)) != k or len(set(rids)) != k or consulta in ids:
        raise ValueError("duplicados o propio registro")
    distancias = [r["distancia"] for r in resultados]
    if any(not math.isfinite(d) or not 0 <= d <= 2 for d in distancias):
        raise ValueError("distancia coseno invalida")
    if list(zip(distancias, rids, strict=True)) != sorted(zip(distancias, rids, strict=True)):
        raise ValueError("resultados desordenados")


def clasificar(resultados, referencia):
    """Empates exactos en distancia double, como en #191; no etiquetas semanticas."""
    ref_ids = {r["id"] for r in referencia}
    umbral = referencia[-1]["distancia"]
    estrictos = {r["id"] for r in referencia if r["distancia"] < umbral}
    obtenidos = {r["id"] for r in resultados}
    cubiertos = len(obtenidos & estrictos)
    empates = sum(r["distancia"] == umbral for r in resultados)
    recall = (cubiertos + min(K - len(estrictos), empates)) / K
    return {
        "clase": "exito" if recall == 1 else "error",
        "recall5": len(obtenidos & ref_ids) / K,
        "recall5_empates": recall,
        "faltantes_exactos": sorted(ref_ids - obtenidos),
    }


def preparar(dataset, corrida, semilla=SEMILLA):
    import quipudb_native as native

    from engine.multimedia import features

    def prohibido(*args, **kwargs):
        raise RuntimeError("#194 no permite extraer caracteristicas")

    fuentes = {}
    for raiz, prefijo in ((dataset, "dataset"), (corrida, "corrida")):
        for p in sorted(raiz.rglob("*")):
            if p.is_file():
                fuentes[f"{prefijo}/{p.relative_to(raiz)}"] = sha256(p)
    with (
        patch.object(features, "extract_features", prohibido),
        patch.object(features, "describe", prohibido),
    ):
        datos, meta, vectores = leer_precalculado(dataset)
        if datos["cantidad"] != 1000 or meta["k"] != 64:
            raise ValueError("la galeria utiliza solo datasets de 1000 con K=64")
        modalidad = datos["modalidad"]
        entrada, tablas = validar_corrida(corrida, modalidad, 1000, "COSINE")
        if entrada["vectores_sha256"] != meta["sha256"]["vectores.npy"]:
            raise ValueError("vectores distintos de los usados en #192")
        objetos = {i: (nombre, ruta) for i, nombre, ruta in filas(dataset, datos)}
        seleccion = seleccionar(tablas, semilla)
        casos = []
        with tempfile.TemporaryDirectory(prefix="quipu-galeria-") as temp:
            copia = Path(temp) / "catalogo"
            shutil.copytree(corrida / "catalogo", copia)
            db = native.Database(copia / "catalogo.txt")
            tabla = db.table(modalidad)
            try:
                for q in seleccion:
                    resultados = {}
                    for tecnica, parametro in PARAMETROS.items():
                        consulta = vectores[q - 1].tolist()
                        matches = (
                            native.scan_k_nearest_vector(
                                tabla, "rasgos", consulta, 11, native.VectorMetric.COSINE
                            )
                            if tecnica == "exacto"
                            else db.index(modalidad, tecnica).k_nearest(
                                consulta, 11, native.VectorMetric.COSINE, parametro
                            )
                        )
                        limpios = limpiar(matches, tabla, q)
                        guardados = json.loads(tablas[tecnica][(parametro, 0, q)]["top10"])
                        if [list(rid) for rid, _ in limpios] != guardados:
                            raise ValueError("la consulta nativa difiere del CSV original")
                        por_rid = {(m.rid.page, m.rid.slot): tabla.read(m.rid) for m in matches}
                        vecinos = []
                        for rid, distancia in limpios[:K]:
                            registro = por_rid[rid]
                            i = registro[0]
                            if i not in objetos or registro[1] != objetos[i][0]:
                                raise ValueError("RID no corresponde al manifiesto")
                            if not np.array_equal(
                                np.asarray(registro[3], dtype=np.float32), vectores[i - 1]
                            ):
                                raise ValueError("vector de registro incompatible")
                            vecinos.append(
                                {
                                    "id": i,
                                    "nombre": objetos[i][0],
                                    "rid": list(rid),
                                    "distancia": distancia,
                                }
                            )
                        validar_resultados(vecinos, q)
                        resultados[tecnica] = vecinos
                    control = limpiar(
                        db.index(modalidad, "ivf").k_nearest(
                            vectores[q - 1].tolist(),
                            11,
                            native.VectorMetric.COSINE,
                            entrada["nlist"],
                        ),
                        tabla,
                        q,
                    )
                    control_ids = [list(rid) for rid, _ in control]
                    if control_ids != json.loads(
                        tablas["ivf"][(32, 0, q)]["top10"]
                    ) or control_ids != json.loads(tablas["exacto"][(0, 0, q)]["top10"]):
                        raise ValueError("IVF completo no coincide con el CSV y el exacto")
                    casos.append(
                        {
                            "consulta": q,
                            "nombre": objetos[q][0],
                            "resultados": resultados,
                            "control_ivf_completo": {
                                "nprobe": entrada["nlist"],
                                "coincide_top10_exacto": True,
                            },
                            "evaluacion": {
                                t: clasificar(resultados[t], resultados["exacto"])
                                for t in ("ivf", "hnsw")
                            },
                        }
                    )
            finally:
                db.close(modalidad)
        for nombre, esperado in fuentes.items():
            prefijo, relativa = nombre.split("/", 1)
            if sha256((dataset if prefijo == "dataset" else corrida) / relativa) != esperado:
                raise ValueError("una fuente cambio durante la lectura")
    # Solo se publica procedencia necesaria, no las rutas absolutas de los catalogos.
    return {
        "modalidad": modalidad,
        "cantidad": 1000,
        "semilla": semilla,
        "k": K,
        "metrica": "COSINE",
        "parametros": PARAMETROS,
        "construccion": {"nlist": entrada["nlist"], "m": 16, "ef_construction": 200},
        "candidatos": 11,
        "casos": casos,
        "fuentes": fuentes,
        "extraccion": meta["extraccion"],
        "vocabulario_k": meta["k"],
    }
