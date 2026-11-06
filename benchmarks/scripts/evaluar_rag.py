"""Evaluacion del RAG de Quipucamayoc (issue #253).

Mide por separado la recuperacion en QuipuDB y la respuesta de Gemini: asi se
sabe si una mala respuesta viene de los chunks o del modelo.

Cuatro pasos, cada uno deja lo que produce para el siguiente:

1. ``reescribir``: Gemini reescribe cada pregunta de ``benchmarks/rag/preguntas.json``
   como palabras clave en ingles. Se guarda en ``benchmarks/rag/reescrituras.json``
   y se versiona: medir la recuperacion no vuelve a llamar a Gemini.
2. ``recuperacion``: para cada pregunta, con la consulta original y con la
   reescrita, y con BM25 y TF-IDF, ordena los papers del corpus por su mejor
   chunk y calcula Hit@k y MRR@10 a nivel de paper.
3. ``respuestas``: el RAG completo (reescritura, recuperacion y Gemini), para
   medir la latencia de cada etapa y revisar las respuestas.
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
"""Las respuestas de Gemini se versionan: no se pueden volver a obtener iguales,
y la revision manual (rag_revision.md) se apoya en ellas. Los CSV no."""
GRAFICAS = RAIZ / "docs" / "informe" / "graficas"

KS = (1, 3, 5, 10)
CANDIDATOS = 60
"""Chunks que se piden al indice: alcanzan para ordenar mas de 10 papers."""
RANKINGS = ("BM25", "TF_IDF")
CONSULTAS = ("original", "reescrita")


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


def paso_recuperacion(repeticiones: int) -> None:
    settings, ClienteMotor = _servidor()
    from app.rag.recuperacion import sql_match

    reescrituras = json.loads(REESCRITURAS.read_text(encoding="utf-8"))["consultas"]
    salida = RESULTADOS / f"{_marca()}_rag_recuperacion.csv"
    with ClienteMotor.desde(settings) as motor:
        arxiv = {int(i): str(a) for i, a in motor.query("SELECT id, arxiv_id FROM papers").rows}
        filas = []
        for p in leer_preguntas():
            for tipo in CONSULTAS:
                consulta = p["pregunta"] if tipo == "original" else reescrituras[p["id"]]
                for ranking in RANKINGS:
                    sql = sql_match(consulta, ranking, CANDIDATOS)
                    cliente_ms, motor_ms = [], []
                    for _ in range(repeticiones):
                        inicio = time.perf_counter()
                        resultado = motor.query(sql)
                        cliente_ms.append((time.perf_counter() - inicio) * 1000)
                        motor_ms.append(float(resultado.plan["time_ms"]) if resultado.plan else 0.0)
                    orden = [arxiv[i] for i in papers_en_orden(int(f[1]) for f in resultado.rows)]
                    pos = posicion(orden, p["esperados"])
                    filas.append(
                        {
                            "id": p["id"],
                            "consulta": tipo,
                            "ranking": ranking,
                            "posicion": "" if pos is None else pos,
                            "top10": ";".join(orden[:10]),
                            "tiempo_ms": round(statistics.median(cliente_ms), 3),
                            "tiempo_motor_ms": round(statistics.median(motor_ms), 3),
                        }
                    )
                    print(f"{p['id']} {tipo:9} {ranking:6} posicion={pos}")
    with salida.open("w", encoding="utf-8", newline="") as archivo:
        escritor = csv.DictWriter(archivo, fieldnames=list(filas[0]), lineterminator="\n")
        escritor.writeheader()
        escritor.writerows(filas)
    print(f"escrito {salida.relative_to(RAIZ)}")


# ---------------------------------------------------------------------------
# 3. Respuestas
# ---------------------------------------------------------------------------


def paso_respuestas(pausa_s: float, reintentar: bool) -> None:
    """``reintentar`` repite solo las preguntas que fallaron en la ultima corrida
    y conserva las demas: no gasta cuota de Gemini en lo que ya salio bien."""

    settings, ClienteMotor = _servidor()
    from app.rag.generacion import responder
    from app.rag.llm import GeminiLLM, LLMError

    llm = GeminiLLM.desde(settings)
    rapido = GeminiLLM.para_reescritura(settings)
    salida = RESULTADOS / f"{_marca()}_rag_respuestas.json"
    previos: dict[str, dict] = {}
    if reintentar:
        previos = {r["id"]: r for r in json.loads(_ultimo("*_rag_respuestas.json").read_text(encoding="utf-8"))}
    registros = []
    with ClienteMotor.desde(settings) as motor:
        for p in leer_preguntas():
            if p["id"] in previos and "error" not in previos[p["id"]]:
                registros.append(previos[p["id"]])
                continue
            r = None
            for intento in range(1, 4):
                try:
                    r = responder(motor, llm, p["pregunta"], llm_reescritura=rapido)
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
            fuentes = [
                {
                    "numero": n,
                    "arxiv_id": e.paper.arxiv_id,
                    "titulo": e.paper.titulo,
                    "pagina": e.fragmento.pagina,
                    "citada": n in {c.numero for c in r.citas},
                }
                for n, e in enumerate(r.evidencias, start=1)
            ]
            esperados = set(p["esperados"])
            registros.append(
                {
                    "id": p["id"],
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
                        "reescritura_ms": r.tiempos.reescritura_ms,
                        "recuperacion_ms": r.tiempos.recuperacion_ms,
                        "generacion_ms": r.tiempos.generacion_ms,
                    },
                }
            )
            t = r.tiempos
            print(
                f"{p['id']}: {r.modelo} reescritura {t.reescritura_ms / 1000:.1f}s "
                f"recuperacion {t.recuperacion_ms:.0f}ms generacion {t.generacion_ms / 1000:.1f}s "
                f"esperado citado={registros[-1]['esperado_citado']}"
            )
    salida.write_text(
        json.dumps(registros, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    print(f"escrito {salida.relative_to(RAIZ)}")


# ---------------------------------------------------------------------------
# 4. Informe
# ---------------------------------------------------------------------------

# Paleta categorica validada (skill dataviz, slots 1-3) y tinta de texto.
AZUL, NARANJA, AQUA = "#2a78d6", "#eb6834", "#1baf7a"
TINTA, TINTA_2, GRILLA = "#0b0b0b", "#52514e", "#e7e6e2"


def resumen_recuperacion(filas: list[dict]) -> list[dict]:
    resumen = []
    for tipo in CONSULTAS:
        for ranking in RANKINGS:
            grupo = [f for f in filas if f["consulta"] == tipo and f["ranking"] == ranking]
            posiciones = [int(f["posicion"]) if f["posicion"] else None for f in grupo]
            resumen.append(
                {
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
    fig, ax = plt.subplots(figsize=(7.5, 3.2), layout="constrained")
    nombres = [_nombre(f) for f in resumen]
    valores = [f["mrr@10"] for f in resumen]
    y = list(range(len(resumen)))[::-1]
    ax.barh(y, valores, height=0.5, color=AZUL)
    for yi, v in zip(y, valores, strict=True):
        ax.text(v + 0.01, yi, f"{v:.2f}", va="center", color=TINTA, fontsize=10)
    ax.set_yticks(y, nombres, color=TINTA)
    ax.set_xlim(0, 1)
    ax.set_xlabel("MRR@10 (1 = el paper esperado sale primero siempre)", color=TINTA_2)
    ax.set_title("Recuperación de papers: MRR@10 por ranking y consulta", loc="left", color=TINTA)
    _estilo(ax)
    return fig


def figura_latencia(plt, respuestas: list[dict]):
    ok = [r for r in respuestas if "tiempos" in r]
    etapas = [
        ("reescritura_ms", "Reescritura (Gemini)", AZUL),
        ("recuperacion_ms", "Recuperación (QuipuDB)", NARANJA),
        ("generacion_ms", "Generación (Gemini)", AQUA),
    ]
    medias = [statistics.mean(r["tiempos"][clave] for r in ok) / 1000 for clave, _, _ in etapas]
    fig, ax = plt.subplots(figsize=(7.5, 2.6), layout="constrained")
    izquierda = 0.0
    for (_, nombre, color), valor in zip(etapas, medias, strict=True):
        texto = f"{valor * 1000:.0f} ms" if valor < 1 else f"{valor:.2f} s"
        # Borde blanco: los 2px de separacion entre segmentos apilados.
        ax.barh([0], [valor], left=izquierda, height=0.5, color=color, edgecolor="white",
                linewidth=2, label=f"{nombre}: {texto}")
        izquierda += valor
    ax.set_ylim(-0.6, 0.6)
    ax.set_yticks([])
    ax.set_xlabel("segundos, promedio por pregunta", color=TINTA_2)
    ax.set_title(f"En qué se va el tiempo de una respuesta ({len(ok)} preguntas)", loc="left", color=TINTA)
    fig.legend(loc="outside lower center", ncol=3, frameon=False, fontsize=9, labelcolor=TINTA)
    _estilo(ax)
    return fig, dict(zip([e[0] for e in etapas], medias, strict=True))


def _pct(valor: float) -> str:
    return f"{valor * 100:.0f} %"


def paso_informe() -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    ruta_rec = _ultimo("*_rag_recuperacion.csv")
    ruta_resp = _ultimo("*_rag_respuestas.json")
    with ruta_rec.open(encoding="utf-8") as archivo:
        filas = list(csv.DictReader(archivo))
    respuestas = json.loads(ruta_resp.read_text(encoding="utf-8"))
    resumen = resumen_recuperacion(filas)

    GRAFICAS.mkdir(parents=True, exist_ok=True)
    with plt.rc_context({"font.family": "DejaVu Sans", "svg.hashsalt": "quipudb-issue253"}):
        fig = figura_mrr(plt, resumen)
        for ext in ("png", "svg"):
            fig.savefig(GRAFICAS / f"253_mrr_recuperacion.{ext}", dpi=160)
        plt.close(fig)
        fig, medias = figura_latencia(plt, respuestas)
        for ext in ("png", "svg"):
            fig.savefig(GRAFICAS / f"253_latencia_etapas.{ext}", dpi=160)
        plt.close(fig)

    ok = [r for r in respuestas if "tiempos" in r]
    preguntas = {p["id"]: p for p in leer_preguntas()}
    por_pregunta = {}
    for f in filas:
        por_pregunta.setdefault(f["id"], {})[(f["consulta"], f["ranking"])] = f["posicion"] or "—"

    lineas = [
        "# Evaluación del RAG de Quipucamayoc (#253)",
        "",
        "<!-- Generado por benchmarks/scripts/evaluar_rag.py informe. No editar a mano. -->",
        "",
        (f"{len(preguntas)} preguntas en español ([benchmarks/rag/preguntas.json](../../benchmarks/rag/preguntas.json)),"
        " cada una con el paper del corpus que debería recuperar. La recuperación se mide aparte de"
        " Gemini: así se sabe si una mala respuesta viene de los chunks o del modelo. La revisión de"
        " las respuestas está en [rag_revision.md](rag_revision.md)."),
        "",
        f"Fuentes: `{ruta_rec.relative_to(RAIZ).as_posix()}` y `{ruta_resp.relative_to(RAIZ).as_posix()}`.",
        "",
        "## Recuperación de papers",
        "",
        ("Los papers se ordenan por el mejor puntaje de sus chunks (`MATCH` con 60 candidatos). Hit@k es"
        " la fracción de preguntas cuyo paper esperado aparece entre los k primeros; MRR@10 promedia"
        " 1/posición."),
        "",
        "| Ranking | Consulta | Hit@1 | Hit@3 | Hit@5 | Hit@10 | MRR@10 | Mediana por consulta |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in resumen:
        lineas.append(
            f"| {'TF-IDF' if r['ranking'] == 'TF_IDF' else 'BM25'} | "
            f"{'pregunta en español' if r['consulta'] == 'original' else 'reescrita en inglés'} | "
            + " | ".join(_pct(r[f"hit@{k}"]) for k in KS)
            + f" | {r['mrr@10']:.2f} | {r['tiempo_ms']:.0f} ms (motor {r['tiempo_motor_ms']:.0f} ms) |"
        )
    lineas += [
        "",
        "![MRR@10 por ranking y consulta](graficas/253_mrr_recuperacion.png)",
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
        "| Pregunta | BM25 español | BM25 inglés | TF-IDF español | TF-IDF inglés |",
        "|---|---|---|---|---|",
    ]
    for pid, pregunta in preguntas.items():
        pos = por_pregunta.get(pid, {})
        lineas.append(
            f"| {pid}: {pregunta['pregunta']} | "
            + " | ".join(str(pos.get((c, r), "—")) for r in RANKINGS for c in CONSULTAS)
            + " |"
        )
    citado = sum(r["esperado_citado"] for r in ok)
    en_fuentes = sum(r["esperado_en_fuentes"] for r in ok)
    descartadas = sum(len(r["citas_descartadas"]) for r in ok)
    modelos = sorted({r["modelo"] or "sin llamar" for r in ok})
    lineas += [
        "",
        "## Respuestas completas",
        "",
        (f"{len(ok)} de {len(respuestas)} preguntas respondidas, con BM25, k = 8 y la reescritura"
        f" activada. Modelos que respondieron: {', '.join(modelos)}."),
        "",
        "| Medida | Valor |",
        "|---|---|",
        f"| El paper esperado estuvo entre las fuentes enviadas a Gemini | {en_fuentes} de {len(ok)} |",
        f"| Gemini citó el paper esperado | {citado} de {len(ok)} |",
        f"| Citas a fuentes inexistentes, quitadas del texto | {descartadas} |",
        f"| Reescritura (promedio) | {medias['reescritura_ms']:.2f} s |",
        f"| Recuperación en QuipuDB (promedio) | {medias['recuperacion_ms'] * 1000:.0f} ms |",
        f"| Generación (promedio) | {medias['generacion_ms']:.2f} s |",
        "",
        "![Tiempo por etapa](graficas/253_latencia_etapas.png)",
        "",
        "## Cómo reproducirlo",
        "",
        ("Con el motor levantado y el corpus cargado (`python -m app.corpus.carga`), desde"
        " `quipucamayoc/server`:"),
        "",
        "```bash",
        ".venv/Scripts/python ../../benchmarks/scripts/evaluar_rag.py reescribir     # opcional: ya está versionada",
        ".venv/Scripts/python ../../benchmarks/scripts/evaluar_rag.py recuperacion",
        ".venv/Scripts/python ../../benchmarks/scripts/evaluar_rag.py respuestas     # llama a Gemini",
        ".venv/Scripts/python ../../benchmarks/scripts/evaluar_rag.py informe        # pide matplotlib",
        "```",
        "",
    ]
    INFORME.write_text("\n".join(lineas), encoding="utf-8", newline="\n")
    RESPUESTAS_INFORME.write_text(ruta_resp.read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
    manifiesto = {
        "descripcion": (
            "Corridas de la evaluacion del RAG (#253), relativas a benchmarks/results."
            " El CSV esta ignorado por Git; las respuestas se copian a rag_respuestas.json."
        ),
        "archivos": {
            clave: {"nombre": ruta.name, "sha256": hashlib.sha256(ruta.read_bytes()).hexdigest()}
            for clave, ruta in (("recuperacion", ruta_rec), ("respuestas", ruta_resp))
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
    sub.add_parser("informe")
    args = parser.parse_args(argv)
    if args.paso == "reescribir":
        paso_reescribir()
    elif args.paso == "recuperacion":
        paso_recuperacion(args.repeticiones)
    elif args.paso == "respuestas":
        paso_respuestas(args.pausa, args.reintentar)
    else:
        paso_informe()


if __name__ == "__main__":
    main()
