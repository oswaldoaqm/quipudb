"""Evaluacion del RAG de Quipucamayoc (issue #253).

Mide por separado la recuperacion en QuipuDB y la respuesta de Gemini: asi se
sabe si una mala respuesta viene de los chunks o del modelo.

Cuatro pasos, cada uno deja lo que produce para el siguiente:

1. ``reescribir``: Gemini reescribe cada pregunta de ``benchmarks/rag/preguntas.json``
   como palabras clave en ingles. Se guarda en ``benchmarks/rag/reescrituras.json``
   y se versiona: medir la recuperacion no vuelve a llamar a Gemini.
2. ``recuperacion``: para cada pregunta y cada busqueda de
   ``CONFIGURACIONES`` (BM25 y TF-IDF con la consulta original y la reescrita,
   HNSW con el embedding de la pregunta e hibrida), ordena los papers del
   corpus por su mejor chunk y calcula Hit@k y MRR@10 a nivel de paper.
3. ``respuestas``: el RAG completo (embedding, reescritura, recuperacion y
   Gemini) en un modo, para medir la latencia de cada etapa y revisar las
   respuestas.
4. ``informe``: tablas en ``docs/informe/rag_evaluacion.md`` y graficas en
   ``docs/informe/graficas/``.

Los pasos 1 a 3 usan el servidor de Quipucamayoc (``quipucamayoc/server``) y el
motor levantado con el corpus cargado; se corren con el Python de ese servidor::

    cd quipucamayoc/server
    .venv/Scripts/python ../../benchmarks/scripts/evaluar_rag.py reescribir
    .venv/Scripts/python ../../benchmarks/scripts/evaluar_rag.py recuperacion
    .venv/Scripts/python ../../benchmarks/scripts/evaluar_rag.py respuestas
    .venv/Scripts/python ../../benchmarks/scripts/evaluar_rag.py informe    # pide matplotlib

Definiciones, con P la lista de papers ordenada por el mejor puntaje de sus
chunks y E los papers esperados de la pregunta:

    posicion  = la del primer paper de E en P (desde 1), o ninguna
    Hit@k     = fraccion de preguntas con posicion <= k
    MRR@10    = promedio de 1/posicion, contando 0 si posicion > 10 o no esta
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import statistics
import sys
import time
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
PREGUNTAS = RAIZ / "benchmarks" / "rag" / "preguntas.json"
REESCRITURAS = RAIZ / "benchmarks" / "rag" / "reescrituras.json"
RESULTADOS = RAIZ / "benchmarks" / "results"
INFORME = RAIZ / "docs" / "informe" / "rag_evaluacion.md"
MANIFIESTO = RAIZ / "docs" / "informe" / "fuentes_rag.json"
RESPUESTAS_INFORME = RAIZ / "docs" / "informe" / "rag_respuestas.json"
RESPUESTAS_HIBRIDO = RAIZ / "docs" / "informe" / "rag_respuestas_hibrido.json"
"""Las respuestas de Gemini se versionan: no se pueden volver a obtener iguales,
y la revision manual (rag_revision.md) se apoya en ellas. Los CSV no."""
GRAFICAS = RAIZ / "docs" / "informe" / "graficas"

KS = (1, 3, 5, 10)
CANDIDATOS = 60
"""Chunks que se piden al indice: alcanzan para ordenar mas de 10 papers."""
RANKINGS = ("BM25", "TF_IDF")
CONSULTAS = ("original", "reescrita")
CONFIGURACIONES: tuple[tuple[str, str, str], ...] = (
    *(("texto", c, r) for c in CONSULTAS for r in RANKINGS),
    # El HNSW recibe siempre la pregunta original: el embedding cruza idiomas.
    ("vectores", "original", "HNSW"),
    # BM25 con la consulta reescrita, mas el HNSW con la original, por RRF.
    ("hibrido", "reescrita", "BM25"),
)
"""``(modo, consulta, ranking)`` que se miden, en el orden del informe (#252)."""


# ---------------------------------------------------------------------------
# Metricas: funciones puras, probadas en benchmarks/test_evaluar_rag.py
# ---------------------------------------------------------------------------


def papers_en_orden(paper_ids: Iterable[int]) -> list[int]:
    """Los papers en el orden de su mejor chunk, sin repetir."""

    return list(dict.fromkeys(paper_ids))


def posicion(ranking: Sequence[str], esperados: Iterable[str]) -> int | None:
    """La posicion (desde 1) del primer paper esperado en ``ranking``."""

    buscados = set(esperados)
    return next((i for i, paper in enumerate(ranking, start=1) if paper in buscados), None)


def hit_at(posiciones: Sequence[int | None], k: int) -> float:
    if not posiciones:
        return 0.0
    return sum(1 for p in posiciones if p is not None and p <= k) / len(posiciones)


def mrr(posiciones: Sequence[int | None], hasta: int = 10) -> float:
    if not posiciones:
        return 0.0
    return sum(1 / p for p in posiciones if p is not None and p <= hasta) / len(posiciones)


# ---------------------------------------------------------------------------
# Entradas y salidas
# ---------------------------------------------------------------------------


def leer_preguntas() -> list[dict]:
    return json.loads(PREGUNTAS.read_text(encoding="utf-8"))["preguntas"]


def _marca() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _ultimo(patron: str) -> Path:
    candidatos = sorted(RESULTADOS.glob(patron))
    if not candidatos:
        sys.exit(f"no hay {patron} en {RESULTADOS}: corre antes el paso que lo genera")
    return candidatos[-1]


def _servidor():
    """Importa el servidor de Quipucamayoc: solo lo necesitan los pasos 1 a 3."""

    servidor = RAIZ / "quipucamayoc" / "server"
    if str(servidor) not in sys.path:
        sys.path.insert(0, str(servidor))
    from app.config import load_settings
    from app.motor import ClienteMotor

    return load_settings(), ClienteMotor


# ---------------------------------------------------------------------------
# 1. Reescritura
# ---------------------------------------------------------------------------


def paso_reescribir() -> None:
    settings, _ = _servidor()
    from app.rag.generacion import reescribir
    from app.rag.llm import GeminiLLM

    llm = GeminiLLM.para_reescritura(settings)
    consultas = {}
    for p in leer_preguntas():
        consulta = reescribir(llm, p["pregunta"])
        consultas[p["id"]] = consulta or p["pregunta"]
        print(f"{p['id']}: {consultas[p['id']]}")
    REESCRITURAS.write_text(
        json.dumps(
            {"modelo": llm.modelo, "fecha": _marca(), "consultas": consultas},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"escrito {REESCRITURAS.relative_to(RAIZ)}")


# ---------------------------------------------------------------------------
# 2. Recuperacion
# ---------------------------------------------------------------------------


def embeddings_preguntas(settings) -> dict[str, list[float]]:
    """El embedding de cada pregunta original, guardado para no volver a pedirlo.

    Va en ``benchmarks/results/`` (ignorado por Git): son 30 x 768 numeros.
    """

    from app.rag.embeddings import GeminiEmbedder, LimiteDeUso

    ruta = RESULTADOS / f"rag_embeddings_preguntas_{settings.gemini_embedding_model}.json"
    guardados = json.loads(ruta.read_text(encoding="utf-8")) if ruta.is_file() else {}
    embedder = GeminiEmbedder.desde(settings)
    for p in leer_preguntas():
        if p["id"] in guardados:
            continue
        for intento in range(1, 6):
            try:
                guardados[p["id"]] = embedder.consulta(p["pregunta"])
                break
            except LimiteDeUso:
                if intento == 5:
                    raise
                time.sleep(60)
        ruta.write_text(json.dumps(guardados), encoding="utf-8", newline="\n")
    return guardados


def ordenar_papers(
    modo: str, texto: Sequence[tuple[int, int]], vectores: Sequence[tuple[int, int]]
) -> list[int]:
    """Los paper_id en el orden de su mejor chunk, segun el modo.

    ``texto`` y ``vectores`` son ``(chunk_id, paper_id)`` en el orden en que los
    devolvio cada busqueda; el hibrido los fusiona con RRF, como la aplicacion.
    """

    from app.rag.recuperacion import fusionar

    if modo == "texto":
        return papers_en_orden(p for _, p in texto)
    if modo == "vectores":
        return papers_en_orden(p for _, p in vectores)
    paper = dict(texto) | dict(vectores)
    fusion = fusionar([[c for c, _ in texto], [c for c, _ in vectores]])
    return papers_en_orden(paper[c] for c, _ in fusion)


def paso_recuperacion(repeticiones: int) -> None:
    settings, ClienteMotor = _servidor()
    from app.rag.recuperacion import sql_match, sql_similar

    reescrituras = json.loads(REESCRITURAS.read_text(encoding="utf-8"))["consultas"]
    vectores = embeddings_preguntas(settings)
    salida = RESULTADOS / f"{_marca()}_rag_recuperacion.csv"
    with ClienteMotor.desde(settings) as motor:
        arxiv = {int(i): str(a) for i, a in motor.query("SELECT id, arxiv_id FROM papers").rows}

        def medir(sql: str) -> tuple[list[tuple[int, int]], float, float]:
            cliente_ms, motor_ms = [], []
            for _ in range(repeticiones):
                inicio = time.perf_counter()
                resultado = motor.query(sql)
                cliente_ms.append((time.perf_counter() - inicio) * 1000)
                motor_ms.append(float(resultado.plan["time_ms"]) if resultado.plan else 0.0)
            pares = [(int(f[0]), int(f[1])) for f in resultado.rows]
            return pares, statistics.median(cliente_ms), statistics.median(motor_ms)

        filas = []
        for p in leer_preguntas():
            for modo, tipo, ranking in CONFIGURACIONES:
                consulta = p["pregunta"] if tipo == "original" else reescrituras[p["id"]]
                texto, vector = [], []
                tiempo = tiempo_motor = 0.0
                if modo != "vectores":
                    texto, t, tm = medir(sql_match(consulta, ranking, CANDIDATOS))
                    tiempo, tiempo_motor = tiempo + t, tiempo_motor + tm
                if modo != "texto":
                    vector, t, tm = medir(sql_similar(vectores[p["id"]], CANDIDATOS))
                    tiempo, tiempo_motor = tiempo + t, tiempo_motor + tm
                orden = [arxiv[i] for i in ordenar_papers(modo, texto, vector)]
                pos = posicion(orden, p["esperados"])
                filas.append(
                    {
                        "id": p["id"],
                        "modo": modo,
                        "consulta": tipo,
                        "ranking": ranking,
                        "posicion": "" if pos is None else pos,
                        "top10": ";".join(orden[:10]),
                        "tiempo_ms": round(tiempo, 3),
                        "tiempo_motor_ms": round(tiempo_motor, 3),
                    }
                )
                print(f"{p['id']} {modo:8} {tipo:9} {ranking:6} posicion={pos}")
    with salida.open("w", encoding="utf-8", newline="") as archivo:
        escritor = csv.DictWriter(archivo, fieldnames=list(filas[0]), lineterminator="\n")
        escritor.writeheader()
        escritor.writerows(filas)
    print(f"escrito {salida.relative_to(RAIZ)}")


# ---------------------------------------------------------------------------
# 3. Respuestas
# ---------------------------------------------------------------------------


def _archivo_respuestas(modo: str) -> str:
    """``*_rag_respuestas.json`` para texto (la corrida original de #253)."""
    return "_rag_respuestas.json" if modo == "texto" else f"_rag_respuestas_{modo}.json"


def paso_respuestas(pausa_s: float, reintentar: bool, modo: str) -> None:
    """``reintentar`` repite solo las preguntas que fallaron en la ultima corrida
    y conserva las demas: no gasta cuota de Gemini en lo que ya salio bien."""

    settings, ClienteMotor = _servidor()
    from app.rag.embeddings import GeminiEmbedder
    from app.rag.generacion import responder
    from app.rag.llm import GeminiLLM, LLMError

    llm = GeminiLLM.desde(settings)
    rapido = GeminiLLM.para_reescritura(settings)
    embedder = GeminiEmbedder.desde(settings) if modo != "texto" else None
    sufijo = _archivo_respuestas(modo)
    salida = RESULTADOS / f"{_marca()}{sufijo}"
    previos: dict[str, dict] = {}
    if reintentar:
        previos = {r["id"]: r for r in json.loads(_ultimo(f"*{sufijo}").read_text(encoding="utf-8"))}
    registros = []
    with ClienteMotor.desde(settings) as motor:
        for p in leer_preguntas():
            if p["id"] in previos and "error" not in previos[p["id"]]:
                registros.append(previos[p["id"]])
                continue
            r = None
            for intento in range(1, 4):
                try:
                    r = responder(
                        motor, llm, p["pregunta"], llm_reescritura=rapido, modo=modo, embedder=embedder
                    )
                    break
                except LLMError as error:
                    # La cuota gratuita de Gemini cuenta peticiones por minuto:
                    # ante el limite se espera y se reintenta.
                    print(f"{p['id']}: Gemini fallo, intento {intento} ({error})")
                    if "limite de uso" not in str(error) or intento == 3:
                        registros.append({"id": p["id"], "pregunta": p["pregunta"], "error": str(error)})
                        break
                    time.sleep(60)
            time.sleep(pausa_s)
            if r is None:
                continue
            citadas = {c.numero for c in r.citas}
            fuentes = [
                {
                    "numero": n,
                    "arxiv_id": e.paper.arxiv_id,
                    "titulo": e.paper.titulo,
                    "pagina": e.fragmento.pagina,
                    "origen": list(e.fragmento.origen),
                    "citada": n in citadas,
                }
                for n, e in enumerate(r.evidencias, start=1)
            ]
            esperados = set(p["esperados"])
            registros.append(
                {
                    "id": p["id"],
                    "modo": modo,
                    "pregunta": p["pregunta"],
                    "esperados": p["esperados"],
                    "consulta": r.consulta,
                    "modelo": r.modelo,
                    "respuesta": r.texto,
                    "citas": [c.numero for c in r.citas],
                    "citas_descartadas": list(r.citas_descartadas),
                    "fuentes": fuentes,
                    "esperado_en_fuentes": any(f["arxiv_id"] in esperados for f in fuentes),
                    "esperado_citado": any(f["arxiv_id"] in esperados and f["citada"] for f in fuentes),
                    "tiempos": {
                        "embedding_ms": r.tiempos.embedding_ms,
                        "reescritura_ms": r.tiempos.reescritura_ms,
                        "recuperacion_ms": r.tiempos.recuperacion_ms,
                        "generacion_ms": r.tiempos.generacion_ms,
                    },
                }
            )
            t = r.tiempos
            print(
                f"{p['id']}: {r.modelo} embedding {t.embedding_ms:.0f}ms "
                f"reescritura {t.reescritura_ms / 1000:.1f}s recuperacion {t.recuperacion_ms:.0f}ms "
                f"generacion {t.generacion_ms / 1000:.1f}s esperado citado={registros[-1]['esperado_citado']}"
            )
    salida.write_text(
        json.dumps(registros, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    print(f"escrito {salida.relative_to(RAIZ)}")


# ---------------------------------------------------------------------------
# 4. Informe
# ---------------------------------------------------------------------------

# Paleta categorica validada (skill dataviz). En la barra de tiempos el
# amarillo va primero: junto al naranja no se distingue.
AZUL, NARANJA, AQUA, AMARILLO = "#2a78d6", "#eb6834", "#1baf7a", "#eda100"
TINTA, TINTA_2, GRILLA = "#0b0b0b", "#52514e", "#e7e6e2"
ETAPAS = (
    ("embedding_ms", "Embedding (Gemini)", AMARILLO),
    ("reescritura_ms", "Reescritura (Gemini)", AZUL),
    ("recuperacion_ms", "Recuperación (QuipuDB)", NARANJA),
    ("generacion_ms", "Generación (Gemini)", AQUA),
)


def resumen_recuperacion(filas: list[dict]) -> list[dict]:
    """Metricas por configuracion, en el orden de ``CONFIGURACIONES``.

    Un CSV de antes de #252 no tiene columna ``modo``: todo es ``texto``.
    """

    resumen = []
    for modo, tipo, ranking in CONFIGURACIONES:
        grupo = [
            f for f in filas
            if f.get("modo", "texto") == modo and f["consulta"] == tipo and f["ranking"] == ranking
        ]
        if not grupo:
            continue
        posiciones = [int(f["posicion"]) if f["posicion"] else None for f in grupo]
        resumen.append(
            {
                "modo": modo,
                "consulta": tipo,
                "ranking": ranking,
                "n": len(grupo),
                **{f"hit@{k}": hit_at(posiciones, k) for k in KS},
                "mrr@10": mrr(posiciones),
                "tiempo_ms": statistics.median(float(f["tiempo_ms"]) for f in grupo),
                "tiempo_motor_ms": statistics.median(float(f["tiempo_motor_ms"]) for f in grupo),
            }
        )
    return resumen


def _nombre(fila: dict) -> str:
    if fila.get("modo", "texto") == "vectores":
        return "HNSW, pregunta en español"
    if fila.get("modo") == "hibrido":
        return "Híbrido: BM25 en inglés + HNSW"
    ranking = "TF-IDF" if fila["ranking"] == "TF_IDF" else "BM25"
    consulta = "pregunta en español" if fila["consulta"] == "original" else "reescrita en inglés"
    return f"{ranking}, {consulta}"


def _estilo(ax) -> None:
    ax.spines[["top", "right", "left"]].set_visible(False)
    ax.spines["bottom"].set_color(GRILLA)
    ax.tick_params(colors=TINTA_2, length=0)
    ax.grid(axis="x", color=GRILLA, linewidth=0.8)
    ax.set_axisbelow(True)


def figura_mrr(plt, resumen: list[dict]):
    fig, ax = plt.subplots(figsize=(7.5, 0.55 * len(resumen) + 1.2), layout="constrained")
    nombres = [_nombre(f) for f in resumen]
    valores = [f["mrr@10"] for f in resumen]
    y = list(range(len(resumen)))[::-1]
    ax.barh(y, valores, height=0.5, color=AZUL)
    for yi, v in zip(y, valores, strict=True):
        ax.text(v + 0.01, yi, f"{v:.2f}", va="center", color=TINTA, fontsize=10)
    ax.set_yticks(y, nombres, color=TINTA)
    ax.set_xlim(0, 1)
    ax.set_xlabel("MRR@10 (1 = el paper esperado sale primero siempre)", color=TINTA_2)
    ax.set_title("Recuperación de papers: MRR@10 por búsqueda", loc="left", color=TINTA)
    _estilo(ax)
    return fig


def medias_por_etapa(respuestas: list[dict]) -> dict[str, float]:
    ok = [r for r in respuestas if "tiempos" in r]
    return {
        clave: statistics.mean(r["tiempos"].get(clave, 0.0) for r in ok) / 1000 for clave, _, _ in ETAPAS
    }


def figura_latencia(plt, por_modo: dict[str, list[dict]]):
    """Una barra apilada por modo: en que se va el tiempo de una respuesta."""

    nombres = {"texto": "Palabras (BM25)", "hibrido": "Híbrido (BM25 + HNSW)", "vectores": "Significado (HNSW)"}
    modos = list(por_modo)
    fig, ax = plt.subplots(figsize=(7.5, 1.0 * len(modos) + 1.6), layout="constrained")
    for fila, modo in enumerate(modos):
        medias = medias_por_etapa(por_modo[modo])
        izquierda = 0.0
        for clave, nombre, color in ETAPAS:
            valor = medias[clave]
            if valor <= 0:
                continue
            # Borde blanco: los 2px de separacion entre segmentos apilados.
            ax.barh([fila], [valor], left=izquierda, height=0.5, color=color, edgecolor="white",
                    linewidth=2, label=nombre if fila == 0 or clave == "embedding_ms" else None)
            izquierda += valor
        ax.text(izquierda + 0.05, fila, f"{izquierda:.1f} s", va="center", color=TINTA, fontsize=10)
    ax.set_yticks(range(len(modos)), [nombres.get(m, m) for m in modos], color=TINTA)
    ax.invert_yaxis()
    ax.set_xlabel("segundos, promedio por pregunta", color=TINTA_2)
    ax.set_title("En qué se va el tiempo de una respuesta", loc="left", color=TINTA)
    manejadores, etiquetas = ax.get_legend_handles_labels()
    unicos = dict(zip(etiquetas, manejadores, strict=True))
    orden = [n for _, n, _ in ETAPAS if n in unicos]
    # Las cuatro etapas no caben en una fila del ancho de la figura: van en dos.
    fig.legend([unicos[n] for n in orden], orden, loc="outside lower center",
               ncol=len(orden) if len(orden) <= 3 else 2, frameon=False, fontsize=9, labelcolor=TINTA)
    _estilo(ax)
    return fig


def _pct(valor: float) -> str:
    return f"{valor * 100:.0f} %"


def _ms(segundos: float) -> str:
    return f"{segundos * 1000:.0f} ms" if segundos < 1 else f"{segundos:.2f} s"


def paso_informe() -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    ruta_rec = _ultimo("*_rag_recuperacion.csv")
    rutas_resp = {"texto": _ultimo("*_rag_respuestas.json")}
    for modo in ("hibrido", "vectores"):
        candidatos = sorted(RESULTADOS.glob(f"*{_archivo_respuestas(modo)}"))
        if candidatos:
            rutas_resp[modo] = candidatos[-1]
    with ruta_rec.open(encoding="utf-8") as archivo:
        filas = list(csv.DictReader(archivo))
    por_modo = {m: json.loads(r.read_text(encoding="utf-8")) for m, r in rutas_resp.items()}
    resumen = resumen_recuperacion(filas)

    GRAFICAS.mkdir(parents=True, exist_ok=True)
    with plt.rc_context({"font.family": "DejaVu Sans", "svg.hashsalt": "quipudb-issue253"}):
        for nombre, fig in (
            ("253_mrr_recuperacion", figura_mrr(plt, resumen)),
            ("253_latencia_etapas", figura_latencia(plt, por_modo)),
        ):
            for ext in ("png", "svg"):
                fig.savefig(GRAFICAS / f"{nombre}.{ext}", dpi=160)
            plt.close(fig)

    preguntas = {p["id"]: p for p in leer_preguntas()}
    configuraciones = [(r["modo"], r["consulta"], r["ranking"]) for r in resumen]
    por_pregunta: dict[str, dict] = {}
    for f in filas:
        clave = (f.get("modo", "texto"), f["consulta"], f["ranking"])
        por_pregunta.setdefault(f["id"], {})[clave] = f["posicion"] or "—"

    fuentes = [f"`{ruta_rec.relative_to(RAIZ).as_posix()}`"] + [
        f"`{r.relative_to(RAIZ).as_posix()}`" for r in rutas_resp.values()
    ]
    lineas = [
        "# Evaluación del RAG de Quipucamayoc (#253, #252)",
        "",
        "<!-- Generado por benchmarks/scripts/evaluar_rag.py informe. No editar a mano. -->",
        "",
        (f"{len(preguntas)} preguntas en español ([benchmarks/rag/preguntas.json](../../benchmarks/rag/preguntas.json)),"
         " cada una con el paper del corpus que debería recuperar. La recuperación se mide aparte de"
         " Gemini: así se sabe si una mala respuesta viene de los chunks o del modelo. La revisión de"
         " las respuestas está en [rag_revision.md](rag_revision.md)."),
        "",
        f"Fuentes: {', '.join(fuentes)}.",
        "",
        "## Recuperación de papers",
        "",
        ("Los papers se ordenan por el mejor puntaje de sus chunks, con 60 candidatos por búsqueda:"
         " `MATCH` (índice invertido, BM25 o TF-IDF), `SIMILAR_TO` (HNSW con coseno sobre el"
         " embedding de la pregunta) o las dos fusionadas con Reciprocal Rank Fusion. Hit@k es la"
         " fracción de preguntas cuyo paper esperado aparece entre los k primeros; MRR@10 promedia"
         " 1/posición. El HNSW recibe siempre la pregunta original en español: el embedding cruza"
         " idiomas."),
        "",
        "| Búsqueda | Hit@1 | Hit@3 | Hit@5 | Hit@10 | MRR@10 | Mediana por consulta |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in resumen:
        lineas.append(
            f"| {_nombre(r)} | "
            + " | ".join(_pct(r[f"hit@{k}"]) for k in KS)
            + f" | {r['mrr@10']:.2f} | {r['tiempo_ms']:.0f} ms (motor {r['tiempo_motor_ms']:.0f} ms) |"
        )
    lineas += [
        "",
        "![MRR@10 por búsqueda](graficas/253_mrr_recuperacion.png)",
        "",
        (
            "**Un sesgo a tener en cuenta.** Las preguntas se escribieron mirando los títulos de los"
            " papers, así que comparten vocabulario con ellos y favorecen a un buscador léxico: las"
            " cifras absolutas son optimistas frente a preguntas reales. La comparación entre la"
            " pregunta en español y la reescrita sí es justa, porque las dos parten de la misma"
            " pregunta: el corpus está en inglés, y sin reescribir BM25 solo coincide en siglas y"
            " nombres propios."
        ),
        "",
        "### Posición del paper esperado, por pregunta",
        "",
        "| Pregunta | " + " | ".join(_nombre({"modo": m, "consulta": c, "ranking": r}) for m, c, r in configuraciones) + " |",
        "|---|" + "---|" * len(configuraciones),
    ]
    for pid, pregunta in preguntas.items():
        pos = por_pregunta.get(pid, {})
        lineas.append(
            f"| {pid}: {pregunta['pregunta']} | "
            + " | ".join(str(pos.get(c, "—")) for c in configuraciones)
            + " |"
        )

    nombres_modo = {"texto": "Palabras (BM25)", "hibrido": "Híbrido", "vectores": "Significado (HNSW)"}
    modos = list(por_modo)
    lineas += [
        "",
        "## Respuestas completas",
        "",
        ("Con BM25, k = 8 y la reescritura activada; el modo híbrido suma el HNSW con el embedding"
         " de la pregunta original."),
        "",
        "| Medida | " + " | ".join(nombres_modo.get(m, m) for m in modos) + " |",
        "|---|" + "---|" * len(modos),
    ]
    filas_tabla: list[tuple[str, list[str]]] = []
    for etiqueta, calcular in (
        ("Preguntas respondidas", lambda rs, ok: f"{len(ok)} de {len(rs)}"),
        ("El paper esperado estuvo entre las fuentes enviadas a Gemini",
         lambda rs, ok: f"{sum(r['esperado_en_fuentes'] for r in ok)} de {len(ok)}"),
        ("Gemini citó el paper esperado", lambda rs, ok: f"{sum(r['esperado_citado'] for r in ok)} de {len(ok)}"),
        ("Citas a fuentes inexistentes, quitadas del texto",
         lambda rs, ok: str(sum(len(r["citas_descartadas"]) for r in ok))),
        ("Modelos que respondieron", lambda rs, ok: ", ".join(sorted({r["modelo"] or "sin llamar" for r in ok}))),
    ):
        filas_tabla.append(
            (etiqueta, [calcular(por_modo[m], [r for r in por_modo[m] if "tiempos" in r]) for m in modos])
        )
    for clave, nombre, _ in ETAPAS:
        filas_tabla.append(
            (f"{nombre} (promedio)", [_ms(medias_por_etapa(por_modo[m])[clave]) for m in modos])
        )
    lineas += [f"| {etiqueta} | " + " | ".join(valores) + " |" for etiqueta, valores in filas_tabla]
    lineas += [
        "",
        "![Tiempo por etapa](graficas/253_latencia_etapas.png)",
        "",
        "## Cómo reproducirlo",
        "",
        ("Con el motor levantado, el corpus cargado (`python -m app.corpus.carga`) y sus embeddings"
         " (`python -m app.corpus.vectores embeber` y `cargar`), desde `quipucamayoc/server`:"),
        "",
        "```bash",
        ".venv/Scripts/python ../../benchmarks/scripts/evaluar_rag.py reescribir     # opcional: ya está versionada",
        ".venv/Scripts/python ../../benchmarks/scripts/evaluar_rag.py recuperacion   # embebe las 30 preguntas una vez",
        ".venv/Scripts/python ../../benchmarks/scripts/evaluar_rag.py respuestas     # llama a Gemini",
        ".venv/Scripts/python ../../benchmarks/scripts/evaluar_rag.py respuestas --modo hibrido",
        ".venv/Scripts/python ../../benchmarks/scripts/evaluar_rag.py informe        # pide matplotlib",
        "```",
        "",
    ]
    INFORME.write_text("\n".join(lineas), encoding="utf-8", newline="\n")
    RESPUESTAS_INFORME.write_text(
        rutas_resp["texto"].read_text(encoding="utf-8"), encoding="utf-8", newline="\n"
    )
    if "hibrido" in rutas_resp:
        RESPUESTAS_HIBRIDO.write_text(
            rutas_resp["hibrido"].read_text(encoding="utf-8"), encoding="utf-8", newline="\n"
        )
    manifiesto = {
        "descripcion": (
            "Corridas de la evaluacion del RAG (#253, #252), relativas a benchmarks/results."
            " El CSV esta ignorado por Git; las respuestas se copian a rag_respuestas*.json."
        ),
        "archivos": {
            clave: {"nombre": ruta.name, "sha256": hashlib.sha256(ruta.read_bytes()).hexdigest()}
            for clave, ruta in (
                ("recuperacion", ruta_rec),
                *((f"respuestas_{m}", r) for m, r in rutas_resp.items()),
            )
        },
    }
    MANIFIESTO.write_text(json.dumps(manifiesto, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"escrito {INFORME.relative_to(RAIZ)} y las graficas 253_* en {GRAFICAS.relative_to(RAIZ)}")


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    sub = parser.add_subparsers(dest="paso", required=True)
    sub.add_parser("reescribir")
    rec = sub.add_parser("recuperacion")
    rec.add_argument("--repeticiones", type=int, default=3, help="para la mediana de la latencia")
    resp = sub.add_parser("respuestas")
    resp.add_argument("--pausa", type=float, default=5.0, help="segundos entre preguntas (cuota de Gemini)")
    resp.add_argument("--reintentar", action="store_true", help="repetir solo las que fallaron la ultima vez")
    resp.add_argument("--modo", choices=["texto", "vectores", "hibrido"], default="texto")
    sub.add_parser("informe")
    args = parser.parse_args(argv)
    if args.paso == "reescribir":
        paso_reescribir()
    elif args.paso == "recuperacion":
        paso_recuperacion(args.repeticiones)
    elif args.paso == "respuestas":
        paso_respuestas(args.pausa, args.reintentar, args.modo)
    else:
        paso_informe()


if __name__ == "__main__":
    main()
