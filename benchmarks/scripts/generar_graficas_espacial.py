"""Graficas y tablas del 2.2.4 (#132): secuencial vs R-Tree vs GiST; nunca ejecuta benchmarks.

Lee exclusivamente las dos corridas oficiales fijadas en
`docs/informe/fuentes_espacial.json` -- la de QuipuDB (#131) y la de PostGIS
(#132) -- y falla antes de exportar si falta un archivo o cambio su SHA-256.
Escribe PNG (300 dpi) y SVG en `docs/informe/graficas/espacial/` y reemplaza el
bloque entre los marcadores BEGIN/END RESULTADOS de
`docs/informe/comparacion_espacial.md`; el resto del informe se conserva.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
MANIFIESTO = RAIZ / "docs" / "informe" / "fuentes_espacial.json"
ENTRADA = RAIZ / "benchmarks" / "results"
SALIDA = RAIZ / "docs" / "informe" / "graficas" / "espacial"
INFORME = RAIZ / "docs" / "informe" / "comparacion_espacial.md"
TAMANOS = (1_000, 10_000, 100_000)
RADIOS = (1, 5, 10)
RADIOS_CRUCE = (1, 5, 10, 25, 50, 100, 250, 500, 2_000)
VALORES_K = (10, 50, 100)
METRICAS = {"haversine": "Haversine", "euclidiana": "euclidiana"}
INICIO = "<!-- BEGIN RESULTADOS -->"
FIN = "<!-- END RESULTADOS -->"

# Orden fijo de la paleta Okabe-Ito que ya usan las graficas de la Parte 1.
# Validada con el validador de dataviz: el rosa queda en la banda 6-8 de CVD y
# bajo 3:1 de contraste, asi que lleva linea discontinua, marcador propio,
# etiqueta directa y tablas con todos los valores.
SERIES = {
    "secuencial": ("Búsqueda secuencial", "#D55E00", "s", "-"),
    "rtree": ("R-Tree (QuipuDB)", "#0072B2", "o", "-"),
    "postgis_gist": ("GiST (PostGIS)", "#009E73", "^", "-"),
}
GIST_GEOMETRY = ("GiST geometry (PostGIS)", "#CC79A7", "D", "--")
# Bytes por pagina: QuipuDB usa paginas de 4 KiB y PostgreSQL bloques de 8 KiB.
PAGINA = {"secuencial": 4_096, "rtree": 4_096, "postgis_gist": 8_192}


def sha256(ruta: Path) -> str:
    return hashlib.sha256(ruta.read_bytes()).hexdigest()


def cargar(entrada: Path, manifiesto: Path) -> tuple[dict, dict]:
    """(resumen por (caso, N), entorno por clave) de las dos corridas, verificadas."""
    fuentes = json.loads(manifiesto.read_text(encoding="utf-8"))
    resumen, entorno = {}, {}
    for corrida in fuentes["corridas"].values():
        for tipo in ("resumen", "entorno", "mediciones"):
            archivo = corrida["archivos"][tipo]
            ruta = entrada / archivo["nombre"]
            if not ruta.is_file():
                raise ValueError(f"falta la fuente oficial {ruta}")
            if sha256(ruta) != archivo["sha256"]:
                raise ValueError(f"{ruta.name} no coincide con el SHA-256 del manifiesto")
        with (entrada / corrida["archivos"]["resumen"]["nombre"]).open(encoding="utf-8") as f:
            for fila in csv.DictReader(f):
                resumen[(fila["caso"], int(fila["n_registros"]))] = fila
        with (entrada / corrida["archivos"]["entorno"]["nombre"]).open(encoding="utf-8") as f:
            for fila in csv.DictReader(f):
                entorno[fila["clave"]] = fila["valor"]
    return resumen, entorno


def ms_por_consulta(fila: dict, campo: str = "mediana_tiempo_ns") -> float:
    return float(fila[campo]) / int(fila["n_operaciones"]) / 1e6


def kib_por_consulta(fila: dict, tecnica: str) -> float:
    paginas = float(fila["mediana_paginas_leidas"]) / int(fila["n_operaciones"])
    return paginas * PAGINA[tecnica] / 1_024


def caso(tecnica: str, operacion: str, metrica: str) -> str:
    return f"{tecnica}_{operacion}_{metrica}"


def guardar(fig, carpeta: Path, nombre: str) -> None:
    for formato in ("png", "svg"):
        ruta = carpeta / f"{nombre}.{formato}"
        if formato == "svg":
            buffer = io.StringIO()
            fig.savefig(buffer, format="svg", metadata={"Date": None})
            texto = "\n".join(linea.rstrip() for linea in buffer.getvalue().splitlines()) + "\n"
            ruta.write_text(texto, encoding="utf-8")
        else:
            fig.savefig(ruta, dpi=300, metadata={"Software": "QuipuDB issue #132"})


def estilo(ax, titulo: str, xlabel: str, ylabel: str | None) -> None:
    ax.set_title(titulo, fontsize=11)
    ax.set_xlabel(xlabel, fontsize=9)
    if ylabel:
        ax.set_ylabel(ylabel, fontsize=9)
    ax.grid(True, color="#e3e3e0", linewidth=0.8)
    ax.set_axisbelow(True)
    for lado in ("top", "right"):
        ax.spines[lado].set_visible(False)
    for lado in ("left", "bottom"):
        ax.spines[lado].set_color("#b9b9b4")
    ax.tick_params(colors="#55554f", labelsize=8)


def _etiqueta_ms(valor: float) -> str:
    """Tres cifras significativas sin notacion cientifica: 0.153, 16.5, 1 946."""
    return f"{valor:,.0f}".replace(",", " ") if valor >= 100 else f"{valor:.3g}"


def linea(ax, xs, filas, serie, *, etiquetar: bool) -> None:
    """Mediana con barras minimo-maximo y etiqueta directa al final."""
    nombre, color, marcador, trazo = serie
    medianas = [ms_por_consulta(f) for f in filas]
    minimos = [ms_por_consulta(f, "min_tiempo_ns") for f in filas]
    maximos = [ms_por_consulta(f, "max_tiempo_ns") for f in filas]
    ax.errorbar(
        xs,
        medianas,
        yerr=(
            [m - lo for m, lo in zip(medianas, minimos, strict=True)],
            [hi - m for m, hi in zip(medianas, maximos, strict=True)],
        ),
        label=nombre,
        color=color,
        marker=marcador,
        linestyle=trazo,
        linewidth=2,
        markersize=6,
        markeredgecolor="white",
        markeredgewidth=1.2,
        capsize=3,
        elinewidth=1,
    )
    if etiquetar:
        ax.annotate(
            f"{_etiqueta_ms(medianas[-1])} ms",
            (xs[-1], medianas[-1]),
            xytext=(6, 0),
            textcoords="offset points",
            va="center",
            fontsize=8,
            color="#33332f",
        )


def eje_tamanos(ax) -> None:
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xticks(TAMANOS, ("1 000", "10 000", "100 000"))
    ax.minorticks_off()
    ax.set_xlim(700, 250_000)


def figura_consultas(plt, resumen, metrica: str, tipo: str):
    """Tres paneles, uno por radio o por k: tiempo por consulta frente a N."""
    parametros = RADIOS if tipo == "radio" else VALORES_K
    fig, ejes = plt.subplots(1, 3, figsize=(12, 4.3), layout="constrained", sharey=True)
    for i, (ax, parametro) in enumerate(zip(ejes, parametros, strict=True)):
        operacion = f"radio_{parametro}km" if tipo == "radio" else f"knn_{parametro}"
        for tecnica, serie in SERIES.items():
            filas = [resumen[(caso(tecnica, operacion, metrica), n)] for n in TAMANOS]
            linea(ax, TAMANOS, filas, serie, etiquetar=True)
        eje_tamanos(ax)
        titulo = f"Radio {parametro} km" if tipo == "radio" else f"k = {parametro}"
        estilo(
            ax,
            titulo,
            "N (puntos) · escala log",
            "Tiempo por consulta (ms) · escala log" if i == 0 else None,
        )
    ejes[0].legend(loc="upper left", fontsize=8, frameon=False)
    nombre = "Consultas por radio" if tipo == "radio" else "k vecinos más cercanos"
    fig.suptitle(f"{nombre} · distancia {METRICAS[metrica]}", fontsize=13)
    fig.supxlabel(
        "Mediana del promedio de 100 consultas; barras: mínimo–máximo de 5 repeticiones. "
        "GiST: tiempo de servidor (EXPLAIN ANALYZE).",
        fontsize=8,
        color="#55554f",
    )
    return fig


def figura_cruce(plt, resumen):
    """Tiempo y KiB accedidos por consulta frente al radio, a 100k: dos paneles, un eje cada uno."""
    n = TAMANOS[-1]
    fig, (izq, der) = plt.subplots(1, 2, figsize=(12, 4.5), layout="constrained")
    for tecnica, serie in SERIES.items():
        filas = [resumen[(caso(tecnica, f"radio_{r}km", "haversine"), n)] for r in RADIOS_CRUCE]
        linea(izq, RADIOS_CRUCE, filas, serie, etiquetar=False)
        nombre, color, marcador, trazo = serie
        der.plot(
            RADIOS_CRUCE,
            [kib_por_consulta(f, tecnica) for f in filas],
            label=nombre,
            color=color,
            marker=marcador,
            linestyle=trazo,
            linewidth=2,
            markersize=6,
            markeredgecolor="white",
            markeredgewidth=1.2,
        )
    for ax in (izq, der):
        ax.set_xscale("log")
        ax.set_yscale("log")
        ax.set_xticks(RADIOS_CRUCE, [f"{r:g}" for r in RADIOS_CRUCE])
        ax.minorticks_off()
    estilo(izq, "Tiempo por consulta", "Radio (km) · escala log", "ms · escala log")
    estilo(
        der,
        "Datos accedidos por consulta",
        "Radio (km) · escala log",
        "KiB (accesos a página × tamaño de página) · escala log",
    )
    izq.legend(loc="upper left", fontsize=8, frameon=False)
    fig.suptitle("Cruce: radios crecientes sobre 100 000 puntos · Haversine", fontsize=13)
    fig.supxlabel(
        "QuipuDB cuenta páginas de 4 KiB; PostgreSQL, accesos a bloques de 8 KiB (un bloque "
        "visitado varias veces cuenta varias veces). GiST forzado con enable_seqscan = off.",
        fontsize=8,
        color="#55554f",
    )
    return fig


def figura_construccion(plt, resumen):
    fig, ax = plt.subplots(figsize=(7.5, 4.6), layout="constrained")
    series = (
        ("rtree_construccion_indice", SERIES["rtree"]),
        (
            "postgis_gist_construccion_indice_haversine",
            ("GiST geography (PostGIS)",) + SERIES["postgis_gist"][1:],
        ),
        ("postgis_gist_construccion_indice_euclidiana", GIST_GEOMETRY),
    )
    for nombre_caso, serie in series:
        filas = [resumen[(nombre_caso, n)] for n in TAMANOS]
        linea(ax, TAMANOS, filas, serie, etiquetar=True)
    eje_tamanos(ax)
    estilo(ax, "Construcción del índice", "N (puntos) · escala log", "ms · escala log")
    ax.legend(loc="upper left", fontsize=8, frameon=False)
    fig.supxlabel(
        "R-Tree: create_index + flush (inserción uno a uno). GiST: CREATE INDEX desde el "
        "cliente. Mediana y mínimo–máximo de 5 repeticiones.",
        fontsize=8,
        color="#55554f",
    )
    return fig


def espacios(resumen) -> list[tuple[str, str, float]]:
    """(sistema, estructura, MiB) a 100k."""
    n = TAMANOS[-1]
    construccion = resumen[("rtree_construccion_indice", n)]
    geog = resumen[("postgis_gist_construccion_indice_haversine", n)]
    geom = resumen[("postgis_gist_construccion_indice_euclidiana", n)]
    mib = 1_048_576
    return [
        ("QuipuDB", "Heap", float(construccion["mediana_datos_bytes"]) / mib),
        ("QuipuDB", "R-Tree", float(construccion["mediana_indices_bytes"]) / mib),
        ("PostgreSQL", "Tabla", float(geog["mediana_datos_bytes"]) / mib),
        ("PostgreSQL", "GiST geography", float(geog["mediana_indices_bytes"]) / mib),
        ("PostgreSQL", "GiST geometry", float(geom["mediana_indices_bytes"]) / mib),
    ]


def figura_espacio(plt, resumen):
    datos = espacios(resumen)
    colores = {"QuipuDB": SERIES["rtree"][1], "PostgreSQL": SERIES["postgis_gist"][1]}
    fig, ax = plt.subplots(figsize=(7.5, 3.8), layout="constrained")
    posiciones = list(range(len(datos)))[::-1]
    ax.barh(
        posiciones,
        [d[2] for d in datos],
        height=0.55,
        color=[colores[d[0]] for d in datos],
        edgecolor="white",
        linewidth=2,
    )
    for y, (_, _, valor) in zip(posiciones, datos, strict=True):
        ax.annotate(
            f"{valor:.1f} MiB",
            (valor, y),
            xytext=(4, 0),
            textcoords="offset points",
            va="center",
            fontsize=8,
            color="#33332f",
        )
    ax.set_yticks(posiciones, [f"{s} · {e}" for s, e, _ in datos])
    estilo(ax, "Espacio en disco con 100 000 puntos", "MiB", None)
    ax.grid(axis="y", visible=False)
    ax.set_xlim(0, max(d[2] for d in datos) * 1.18)
    fig.supxlabel(
        "QuipuDB: tamaño de archivo. PostgreSQL: pg_relation_size (fork principal); "
        "la tabla geometry ocupa lo mismo que la geography.",
        fontsize=8,
        color="#55554f",
    )
    return fig


def _fmt(valor: float) -> str:
    """Decimal con coma: tres decimales bajo 10, uno por encima."""
    texto = f"{valor:.3f}" if valor < 10 else f"{valor:.1f}"
    return texto.replace(".", ",")


def _miles(valor: float) -> str:
    """Entero con espacio como separador de miles."""
    return f"{valor:,.0f}".replace(",", " ")


def tablas(resumen, entorno) -> str:
    n = TAMANOS[-1]
    lineas = [
        f"<!-- generado por generar_graficas_espacial.py; N = {_miles(n)} salvo indicacion -->",
        "",
        "### Tiempo y datos accedidos por consulta (N = 100 000)",
        "",
        (
            "| Consulta | Métrica | Secuencial ms | R-Tree ms | GiST ms | R-Tree / GiST "
            "| KiB secuencial | KiB R-Tree | KiB GiST |"
        ),
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    operaciones = [f"radio_{r}km" for r in RADIOS] + [f"knn_{k}" for k in VALORES_K]
    for metrica in METRICAS:
        for operacion in operaciones:
            filas = {t: resumen[(caso(t, operacion, metrica), n)] for t in SERIES}
            tiempos = {t: ms_por_consulta(f) for t, f in filas.items()}
            kib = {t: kib_por_consulta(f, t) for t, f in filas.items()}
            lineas.append(
                f"| {operacion} | {metrica} | {_fmt(tiempos['secuencial'])} | "
                f"{_fmt(tiempos['rtree'])} | {_fmt(tiempos['postgis_gist'])} | "
                f"{_fmt(tiempos['rtree'] / tiempos['postgis_gist'])} | "
                f"{_miles(kib['secuencial'])} | {_miles(kib['rtree'])} | "
                f"{_miles(kib['postgis_gist'])} |"
            )

    lineas += [
        "",
        "### Cruce con radios crecientes (Haversine, N = 100 000)",
        "",
        (
            "| Radio | Devueltos por consulta | Secuencial ms | R-Tree ms | GiST ms "
            "| Plan natural de PostgreSQL |"
        ),
        "|---:|---:|---:|---:|---:|---|",
    ]
    for r in RADIOS_CRUCE:
        operacion = f"radio_{r}km"
        devueltos = int(
            entorno[f"caso.{caso('rtree', operacion, 'haversine')}.{n}.resultados_totales"]
        )
        plan = entorno[f"caso.{caso('postgis_gist', operacion, 'haversine')}.{n}.plan_natural"]
        t = {x: ms_por_consulta(resumen[(caso(x, operacion, "haversine"), n)]) for x in SERIES}
        lineas.append(
            f"| {_miles(r)} km | {_miles(devueltos / 100)} | {_fmt(t['secuencial'])} "
            f"| {_fmt(t['rtree'])} | {_fmt(t['postgis_gist'])} | {plan} |"
        )

    lineas += [
        "",
        "### Construcción y espacio",
        "",
        (
            "| N | R-Tree ms | GiST geography ms | GiST geometry ms | Heap QuipuDB KiB "
            "| R-Tree KiB | Tabla PostgreSQL KiB | GiST geography KiB | GiST geometry KiB |"
        ),
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for m in TAMANOS:
        rt = resumen[("rtree_construccion_indice", m)]
        geog = resumen[("postgis_gist_construccion_indice_haversine", m)]
        geom = resumen[("postgis_gist_construccion_indice_euclidiana", m)]
        valores = [
            float(rt["mediana_tiempo_ns"]) / 1e6,
            float(geog["mediana_tiempo_ns"]) / 1e6,
            float(geom["mediana_tiempo_ns"]) / 1e6,
        ]
        tamanos = [
            float(rt["mediana_datos_bytes"]),
            float(rt["mediana_indices_bytes"]),
            float(geog["mediana_datos_bytes"]),
            float(geog["mediana_indices_bytes"]),
            float(geom["mediana_indices_bytes"]),
        ]
        lineas.append(
            f"| {_miles(m)} | "
            + " | ".join(_fmt(v) for v in valores)
            + " | "
            + " | ".join(_miles(b / 1024) for b in tamanos)
            + " |"
        )
    return "\n".join(lineas) + "\n"


FIGURAS = (
    ("espacial_radio_haversine", lambda plt, r: figura_consultas(plt, r, "haversine", "radio")),
    ("espacial_knn_haversine", lambda plt, r: figura_consultas(plt, r, "haversine", "knn")),
    ("espacial_radio_euclidiana", lambda plt, r: figura_consultas(plt, r, "euclidiana", "radio")),
    ("espacial_knn_euclidiana", lambda plt, r: figura_consultas(plt, r, "euclidiana", "knn")),
    ("espacial_cruce", figura_cruce),
    ("espacial_construccion", figura_construccion),
    ("espacial_espacio", figura_espacio),
)


def generar(entrada: Path, salida: Path, manifiesto: Path, informe: Path) -> list[Path]:
    resumen, entorno = cargar(entrada, manifiesto)
    texto = informe.read_text(encoding="utf-8")
    if texto.count(INICIO) != 1 or texto.count(FIN) != 1:
        raise ValueError(f"{informe} debe tener un {INICIO} y un {FIN}")
    bloque = tablas(resumen, entorno)

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    salida.mkdir(parents=True, exist_ok=True)
    escritas = []
    with plt.rc_context({"font.family": "DejaVu Sans", "svg.hashsalt": "quipudb-issue132"}):
        for nombre, construir in FIGURAS:
            fig = construir(plt, resumen)
            guardar(fig, salida, nombre)
            plt.close(fig)
            escritas += [salida / f"{nombre}.png", salida / f"{nombre}.svg"]

    antes, resto = texto.split(INICIO)
    _, despues = resto.split(FIN)
    informe.write_text(f"{antes}{INICIO}\n{bloque}{FIN}{despues}", encoding="utf-8", newline="\n")
    return escritas


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--entrada", type=Path, default=ENTRADA)
    parser.add_argument("--salida", type=Path, default=SALIDA)
    parser.add_argument("--manifiesto", type=Path, default=MANIFIESTO)
    parser.add_argument("--informe", type=Path, default=INFORME)
    args = parser.parse_args()
    try:
        escritas = generar(args.entrada, args.salida, args.manifiesto, args.informe)
    except (OSError, ValueError, KeyError) as error:
        parser.exit(1, f"error: {error}\n")
    print(f"{len(escritas)} archivos en {args.salida}; tablas en {args.informe}")


if __name__ == "__main__":
    main()
