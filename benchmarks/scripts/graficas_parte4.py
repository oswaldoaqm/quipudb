"""Figuras de #193 a partir de filas validadas; sin acceso al motor."""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from benchmarks.scripts.datos_parte4 import MODALIDADES, TAMANOS, seleccionar

COLORES = {"ivf": "#2563a6", "hnsw": "#cf6619"}
NOMBRES = {"imagenes": "Imágenes", "audio": "Audio"}


def estilo():
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 11,
            "axes.titlesize": 15,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "savefig.facecolor": "white",
            "figure.facecolor": "white",
        }
    )


def base(titulo, ylabel):
    fig, ax = plt.subplots(figsize=(8.4, 5.2), layout="constrained")
    ax.set_title(titulo, loc="left", pad=18)
    ax.set_ylabel(ylabel)
    ax.grid(axis="y", alpha=0.18)
    ax.set_axisbelow(True)
    return fig, ax


def guardar(fig, salida, nombre):
    p = Path(salida) / f"{nombre}.png"
    fig.savefig(p, dpi=300, metadata={"Software": "QuipuDB #193 / Matplotlib"})
    plt.close(fig)
    return p


def construccion(datos, salida):
    archivos = []
    for m in MODALIDADES:
        fig, ax = base(f"Construcción · {NOMBRES[m]}", "Tiempo total (s, escala log)")
        for t, color in COLORES.items():
            filas = [seleccionar(datos, m, n, t) for n in TAMANOS]
            y = [r["total_s"] for r in filas]
            ax.plot(range(3), y, "o-", color=color, label=t.upper(), linewidth=2)
            for i, v in enumerate(y):
                ax.annotate(
                    f"{v:.2f}",
                    (i, v),
                    xytext=(0, 9 if t == "hnsw" else -18),
                    textcoords="offset points",
                    ha="center",
                    fontsize=9,
                )
        ax.set_yscale("log")
        ax.margins(y=0.3)
        ax.set_xticks(range(3), ["1 000", "10 000", "100 000"])
        ax.set_xlabel("Objetos")
        ax.legend(title="Una construcción por caso", frameon=False)
        fig.supxlabel("Fuente #191 · IVF incluye entrenamiento, creación y flush", fontsize=9)
        archivos.append(guardar(fig, salida, f"construccion_{m}"))
    return archivos


def consultas(datos, salida):
    archivos = []
    for m in MODALIDADES:
        fig, ax = base(f"Consulta · {NOMBRES[m]}", "Tiempo por consulta (ms, escala log)")
        for t, color in COLORES.items():
            filas = [seleccionar(datos, m, n, t) for n in TAMANOS]
            y = np.array([r["consulta_ms"] for r in filas])
            err = np.array(
                [
                    [v - r["minimo_ms"] for v, r in zip(y, filas, strict=True)],
                    [r["maximo_ms"] - v for v, r in zip(y, filas, strict=True)],
                ]
            )
            ax.errorbar(range(3), y, yerr=err, fmt="o-", color=color, label=t.upper(), capsize=4)
            for i, r in enumerate(filas):
                etiqueta = ("nprobe" if t == "ivf" else "ef_search") + f"={r['parametro']}"
                ax.annotate(
                    etiqueta,
                    (i, y[i]),
                    xytext=(0, 13 if t == "ivf" else -20),
                    textcoords="offset points",
                    ha="center",
                    fontsize=9,
                )
        ax.set_yscale("log")
        ax.margins(x=0.16, y=0.4)
        ax.set_xticks(range(3), ["1 000", "10 000", "100 000"])
        ax.set_xlabel("Objetos")
        ax.legend(frameon=False, title="Más rápida medida con Recall ≥ 0,99")
        fig.supxlabel(
            "Fuente #191 · Mediana de 5 medias; barras: mínimo–máximo de medias", fontsize=9
        )
        archivos.append(guardar(fig, salida, f"consulta_{m}"))
    return archivos


def recall(datos, salida):
    archivos = []
    for m in MODALIDADES:
        for n in TAMANOS:
            fig, ax = base(
                f"Recall y latencia · {NOMBRES[m]} · {n:,}".replace(",", " "), "Recall@10"
            )
            for t, color in COLORES.items():
                filas = sorted(
                    [r for r in datos if r["dataset"] == f"{m}_{n}" and r["tecnica"] == t],
                    key=lambda r: r["parametro"],
                )
                x = [r["consulta_ms"] for r in filas]
                y = [r["recall10"] for r in filas]
                ax.plot(x, y, "o-", color=color, label=t.upper(), linewidth=1.5)
                for i, r in enumerate(filas):
                    ax.annotate(
                        str(r["parametro"]),
                        (x[i], y[i]),
                        xytext=(0, 11 if t == "ivf" else -16),
                        textcoords="offset points",
                        ha="center",
                        fontsize=8,
                        color=color,
                        bbox={"facecolor": "white", "edgecolor": "none", "alpha": 0.75, "pad": 0.3},
                    )
            ax.set_xscale("log")
            ax.set_ylim(0, 1.08)
            ax.set_xlabel("Tiempo por consulta (ms, escala log)")
            ax.legend(frameon=False, loc="lower right")
            fig.supxlabel(
                "Fuente #191 · Etiquetas: nprobe (IVF), ef_search (HNSW) · Líneas guían la lectura",
                fontsize=9,
            )
            archivos.append(guardar(fig, salida, f"recall_{m}_{n}"))
    return archivos


def metricas(datos, salida):
    archivos = []
    for m in MODALIDADES:
        for t, p in (("ivf", 16), ("hnsw", 80)):
            filas = [
                next(
                    r
                    for r in datos
                    if r["dataset"] == f"{m}_{n}" and r["tecnica"] == t and r["parametro"] == p
                )
                for n in TAMANOS
            ]
            fig, (ax, tabla) = plt.subplots(
                2, 1, figsize=(8.4, 6), gridspec_kw={"height_ratios": [4, 1]}, layout="constrained"
            )
            ax.set_title(f"Euclidiana y coseno · {NOMBRES[m]} · {t.upper()}", loc="left", pad=18)
            for nombre, marca, trazo in (("euclidiana", "s", "-"), ("coseno", "o", "--")):
                ax.plot(
                    range(3),
                    [float(r[nombre + "_ms"]) for r in filas],
                    marker=marca,
                    linestyle=trazo,
                    color=COLORES[t],
                    label=nombre.capitalize(),
                    markerfacecolor="white" if nombre == "coseno" else COLORES[t],
                )
            ax.set_ylim(bottom=0)
            ax.set_ylabel("Tiempo por consulta (ms)")
            ax.set_xticks(range(3), ["1 000", "10 000", "100 000"])
            ax.grid(axis="y", alpha=0.18)
            ax.legend(
                frameon=False,
                title=f"{'nprobe' if t == 'ivf' else 'ef_search'}={p} en ambas métricas",
            )
            tabla.axis("off")
            table = tabla.table(
                cellText=[
                    [
                        f"{float(r['recall_euclidiana']):.3f}",
                        f"{float(r['recall_coseno']):.3f}",
                        f"{float(r['porcentaje']):.1f} %",
                    ]
                    for r in filas
                ],
                rowLabels=["1 000", "10 000", "100 000"],
                colLabels=["Recall euclidiana", "Recall coseno", "Coincidencia Top 10"],
                loc="center",
                cellLoc="center",
            )
            table.auto_set_font_size(False)
            table.set_fontsize(10)
            table.scale(1, 1.5)
            fig.supxlabel(
                "Fuente #192 · Configuración fija · Mediana de 5 medias de 100 consultas",
                fontsize=9,
            )
            archivos.append(guardar(fig, salida, f"metricas_{m}_{t}"))
    return archivos
