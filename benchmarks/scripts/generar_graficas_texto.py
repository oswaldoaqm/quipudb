"""Graficas y tablas del 2.3.2 (#174): TF-IDF vs BM25 vs GIN; nunca ejecuta benchmarks.

Lee exclusivamente las fuentes fijadas en `docs/informe/fuentes_texto.json`: la
corrida de tiempos de QuipuDB (#172), sus rankings (#173), la corrida del GIN
de PostgreSQL con sus rankings (#174) y los CSV de consultas y juicios de #171.
Falla antes de exportar si falta un archivo o cambio su SHA-256. La relevancia
la calcula con el mismo codigo de #173 (`relevancia_texto.py`).

Escribe PNG (300 dpi) y SVG en `docs/informe/graficas/texto/` y reemplaza el
bloque entre los marcadores BEGIN/END RESULTADOS de
`docs/informe/comparacion_texto.md`; el resto del informe se conserva.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

if __package__:
    from .generar_graficas_espacial import (
        FIN,
        INICIO,
        _fmt,
        _miles,
        eje_tamanos,
        estilo,
        guardar,
        linea,
        ms_por_consulta,
        sha256,
    )
    from .relevancia_texto import evaluar_archivos, prueba_pareada, resumir
else:
    from generar_graficas_espacial import (
        FIN,
        INICIO,
        _fmt,
        _miles,
        eje_tamanos,
        estilo,
        guardar,
        linea,
        ms_por_consulta,
        sha256,
    )
    from relevancia_texto import evaluar_archivos, prueba_pareada, resumir

RAIZ = Path(__file__).resolve().parents[2]
MANIFIESTO = RAIZ / "docs" / "informe" / "fuentes_texto.json"
ENTRADA = RAIZ / "benchmarks" / "results"
DATASETS = RAIZ / "benchmarks" / "datasets"
SALIDA = RAIZ / "docs" / "informe" / "graficas" / "texto"
INFORME = RAIZ / "docs" / "informe" / "comparacion_texto.md"
TAMANOS = (1_000, 10_000, 100_000)
LONGITUDES = {"1": "1", "3": "3", "5+": "5omas"}

# Paleta Okabe-Ito, como las graficas de la Parte 2. GIN AND lleva linea
# discontinua: es otra semantica (todos los terminos) y no compite en igualdad.
SERIES = {
    "tf_idf": ("TF-IDF + coseno (QuipuDB)", "#0072B2", "o", "-"),
    "bm25": ("BM25 (QuipuDB)", "#D55E00", "s", "-"),
    "gin_or": ("GIN, OR (PostgreSQL)", "#009E73", "^", "-"),
    "gin_and": ("GIN, AND (PostgreSQL)", "#CC79A7", "D", "--"),
}
# Bytes por pagina: QuipuDB usa paginas de 4 KiB y PostgreSQL bloques de 8 KiB.
PAGINA = {"tf_idf": 4_096, "bm25": 4_096, "gin_or": 8_192, "gin_and": 8_192}
PERMUTACIONES = 10_000


def cargar(entrada: Path, datasets: Path, manifiesto: Path) -> dict:
    """Resumen, entorno por corrida y relevancia, todo de fuentes verificadas."""
    fuentes = json.loads(manifiesto.read_text(encoding="utf-8"))
    for corrida in fuentes["corridas"].values():
        for archivo in corrida["archivos"].values():
            _verificar(entrada / archivo["nombre"], archivo["sha256"])
    for nombre, esperado in fuentes["datasets"].items():
        _verificar(datasets / nombre, esperado)

    resumen, entornos, rankings = {}, {}, []
    for clave, corrida in fuentes["corridas"].items():
        archivos = corrida["archivos"]
        if "resumen" in archivos:
            with (entrada / archivos["resumen"]["nombre"]).open(encoding="utf-8") as f:
                for fila in csv.DictReader(f):
                    resumen[(fila["caso"], int(fila["n_registros"]))] = fila
        with (entrada / archivos["entorno"]["nombre"]).open(encoding="utf-8") as f:
            entornos[clave] = {fila["clave"]: fila["valor"] for fila in csv.DictReader(f)}
        if "rankings" in archivos:
            rankings.append(entrada / archivos["rankings"]["nombre"])
    evaluaciones = evaluar_archivos(rankings, datasets)
    relevancia = {
        (f["tecnica"], f["n_documentos"], f["longitud"]): f for f in resumir(evaluaciones)
    }
    return {
        "resumen": resumen,
        "entornos": entornos,
        "relevancia": relevancia,
        "evaluaciones": evaluaciones,
    }


def _verificar(ruta: Path, esperado: str) -> None:
    if not ruta.is_file():
        raise ValueError(f"falta la fuente oficial {ruta}")
    if sha256(ruta) != esperado:
        raise ValueError(f"{ruta.name} no coincide con el SHA-256 del manifiesto")


def caso_consulta(tecnica: str, longitud: str) -> str:
    return f"{tecnica}_consulta_{LONGITUDES[longitud]}_palabras"


def kib_por_consulta(fila: dict, tecnica: str) -> float:
    paginas = float(fila["mediana_paginas_leidas"]) / int(fila["n_operaciones"])
    return paginas * PAGINA[tecnica] / 1_024


def coincidentes(datos: dict, tecnica: str, longitud: str, n: int) -> float:
    """Documentos con algun termino (QuipuDB, GIN OR) o con todos (GIN AND)."""
    corrida = "172" if tecnica in ("tf_idf", "bm25") else "174"
    clave = f"caso.{caso_consulta(tecnica, longitud)}.{n}.coincidentes_por_consulta"
    return float(datos["entornos"][corrida][clave])


def figura_consultas(plt, datos):
    fig, ejes = plt.subplots(1, 3, figsize=(12, 4.3), layout="constrained", sharey=True)
    for i, (ax, longitud) in enumerate(zip(ejes, LONGITUDES, strict=True)):
        for tecnica, serie in SERIES.items():
            filas = [datos["resumen"][(caso_consulta(tecnica, longitud), n)] for n in TAMANOS]
            linea(ax, TAMANOS, filas, serie, etiquetar=True)
        eje_tamanos(ax)
        estilo(
            ax,
            f"Consultas de {longitud} palabra{'s' if longitud != '1' else ''}",
            "N (documentos) · escala log",
            "Tiempo por consulta (ms) · escala log" if i == 0 else None,
        )
    ejes[0].legend(loc="upper left", fontsize=8, frameon=False)
    fig.suptitle("Búsqueda de texto, top-10", fontsize=13)
    fig.supxlabel(
        "Mediana del promedio de 100 consultas; barras: mínimo–máximo de 5 repeticiones. "
        "QuipuDB: desde Python, incluye el binding. GIN: tiempo de servidor (EXPLAIN ANALYZE).",
        fontsize=8,
        color="#55554f",
    )
    return fig


def figura_datos_accedidos(plt, datos):
    fig, ejes = plt.subplots(1, 3, figsize=(12, 4.3), layout="constrained", sharey=True)
    for i, (ax, longitud) in enumerate(zip(ejes, LONGITUDES, strict=True)):
        # TF-IDF y BM25 leen las mismas paginas (#172): una sola serie.
        for tecnica in ("bm25", "gin_or", "gin_and"):
            nombre, color, marcador, trazo = SERIES[tecnica]
            if tecnica == "bm25":
                nombre = "TF-IDF y BM25 (QuipuDB)"
            filas = [datos["resumen"][(caso_consulta(tecnica, longitud), n)] for n in TAMANOS]
            ax.plot(
                TAMANOS,
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
        eje_tamanos(ax)
        estilo(
            ax,
            f"Consultas de {longitud} palabra{'s' if longitud != '1' else ''}",
            "N (documentos) · escala log",
            "KiB por consulta · escala log" if i == 0 else None,
        )
    ejes[0].legend(loc="upper left", fontsize=8, frameon=False)
    fig.suptitle("Datos accedidos por consulta", fontsize=13)
    fig.supxlabel(
        "QuipuDB cuenta páginas de 4 KiB leídas del índice; PostgreSQL, accesos a bloques "
        "de 8 KiB del índice y de la tabla (un bloque visitado varias veces cuenta varias veces).\n"
        "Con una palabra, GIN OR y GIN AND son la misma consulta y sus líneas se superponen.",
        fontsize=8,
        color="#55554f",
    )
    return fig


def figura_construccion(plt, datos):
    resumen = datos["resumen"]
    fig, ax = plt.subplots(figsize=(7.5, 4.6), layout="constrained")
    linea(
        ax,
        TAMANOS,
        [resumen[("spimi_construccion_indice", n)] for n in TAMANOS],
        ("SPIMI: tokenizar + bloques + mezcla (QuipuDB)", "#0072B2", "o", "-"),
        etiquetar=True,
    )
    linea(
        ax,
        TAMANOS,
        [resumen[("gin_construccion_indice", n)] for n in TAMANOS],
        ("GIN: CREATE INDEX (PostgreSQL)", "#009E73", "^", "-"),
        etiquetar=True,
    )
    linea(
        ax,
        TAMANOS,
        [resumen[("gin_tokenizacion", n)] for n in TAMANOS],
        ("to_tsvector sobre el corpus (PostgreSQL)", "#E69F00", "v", "--"),
        etiquetar=True,
    )
    eje_tamanos(ax)
    estilo(ax, "Construcción del índice", "N (documentos) · escala log", "ms · escala log")
    ax.legend(loc="upper left", fontsize=8, frameon=False)
    fig.supxlabel(
        "El build de QuipuDB tokeniza; el GIN se arma sobre la columna tsvector,\n"
        "que se tokenizó al cargar. Mediana y mínimo–máximo de 5 repeticiones.",
        fontsize=8,
        color="#55554f",
    )
    return fig


def espacios(datos) -> list[tuple[str, str, float]]:
    """(sistema, estructura, MiB) a 100k."""
    n = TAMANOS[-1]
    quipudb = datos["resumen"][("spimi_construccion_indice", n)]
    gin = datos["resumen"][("gin_construccion_indice", n)]
    tsvector = float(datos["entornos"]["174"][f"tabla.{n}.tsvector_bytes"])
    mib = 1_048_576
    return [
        ("QuipuDB", "Heap (VARCHAR(256) fijo)", float(quipudb["mediana_datos_bytes"]) / mib),
        ("QuipuDB", "Índice invertido + RIDs", float(quipudb["mediana_indices_bytes"]) / mib),
        ("PostgreSQL", "Tabla (con tsvector)", float(gin["mediana_datos_bytes"]) / mib),
        ("PostgreSQL", "columna tsvector (dentro de la tabla)", tsvector / mib),
        ("PostgreSQL", "GIN", float(gin["mediana_indices_bytes"]) / mib),
    ]


def figura_espacio(plt, datos):
    filas = espacios(datos)
    colores = {"QuipuDB": SERIES["bm25"][1], "PostgreSQL": SERIES["gin_or"][1]}
    fig, ax = plt.subplots(figsize=(7.5, 3.8), layout="constrained")
    posiciones = list(range(len(filas)))[::-1]
    ax.barh(
        posiciones,
        [f[2] for f in filas],
        height=0.55,
        color=[colores[f[0]] for f in filas],
        edgecolor="white",
        linewidth=2,
    )
    for y, (_, _, valor) in zip(posiciones, filas, strict=True):
        ax.annotate(
            f"{valor:.1f} MiB",
            (valor, y),
            xytext=(4, 0),
            textcoords="offset points",
            va="center",
            fontsize=8,
            color="#33332f",
        )
    ax.set_yticks(posiciones, [f"{s} · {e}" for s, e, _ in filas])
    estilo(ax, "Espacio en disco con 100 000 documentos", "MiB", None)
    ax.grid(axis="y", visible=False)
    ax.set_xlim(0, max(f[2] for f in filas) * 1.18)
    fig.supxlabel(
        "QuipuDB: tamaño de archivo. PostgreSQL: pg_relation_size (fork principal) y "
        "suma de pg_column_size(tsv).",
        fontsize=8,
        color="#55554f",
    )
    return fig


def figura_relevancia(plt, datos):
    relevancia = datos["relevancia"]
    fig, ejes = plt.subplots(2, 3, figsize=(12, 7.2), layout="constrained", sharey="row")
    for fila_ejes, metrica, titulo in (
        (ejes[0], "recall_10_empates", "Recall@10"),
        (ejes[1], "precision_10_empates", "Precision@10"),
    ):
        for i, (ax, longitud) in enumerate(zip(fila_ejes, LONGITUDES, strict=True)):
            for tecnica, (nombre, color, marcador, trazo) in SERIES.items():
                ax.plot(
                    TAMANOS,
                    [relevancia[(tecnica, n, longitud)][metrica] for n in TAMANOS],
                    label=nombre,
                    color=color,
                    marker=marcador,
                    linestyle=trazo,
                    linewidth=2,
                    markersize=6,
                    markeredgecolor="white",
                    markeredgewidth=1.2,
                )
            if metrica.startswith("precision"):
                techo = relevancia[("bm25", TAMANOS[0], longitud)]["techo_precision_10"]
                ax.axhline(techo, color="#77776f", linewidth=1, linestyle=":")
                ax.annotate(
                    "techo",
                    (TAMANOS[0], techo),
                    xytext=(2, 3),
                    textcoords="offset points",
                    fontsize=7,
                    color="#55554f",
                )
            ax.set_xscale("log")
            ax.set_xticks(TAMANOS, ("1 000", "10 000", "100 000"))
            ax.minorticks_off()
            ax.set_xlim(700, 140_000)
            ax.set_ylim(0, 1.05 if metrica.startswith("recall") else 0.18)
            estilo(
                ax,
                f"{titulo} · {longitud} palabra{'s' if longitud != '1' else ''}",
                "N (documentos) · escala log",
                titulo if i == 0 else None,
            )
    ejes[0][0].legend(loc="lower left", fontsize=8, frameon=False)
    fig.suptitle("Relevancia contra los juicios de BEIR Quora", fontsize=13)
    fig.supxlabel(
        "Promedio de 100 consultas por punto; empates en el corte promediados (McSherry y "
        "Najork). El techo de Precision@10 es 0,161: cada consulta tiene 1,61 relevantes.\n"
        "Con una palabra, GIN OR y GIN AND son la misma consulta y sus líneas se superponen.",
        fontsize=8,
        color="#55554f",
    )
    return fig


def pareadas(datos, metrica: str = "recall_10_empates") -> dict:
    """(tecnica, n, longitud) -> (diferencia contra GIN OR, p-valor), para TF-IDF y BM25."""
    por_clave: dict = {}
    for e in datos["evaluaciones"]:
        por_clave.setdefault((e.tecnica, e.n_documentos, e.longitud), {})[e.consulta] = e
    resultado = {}
    for tecnica in ("tf_idf", "bm25"):
        for n in TAMANOS:
            for longitud in LONGITUDES:
                a = por_clave[(tecnica, n, longitud)]
                b = por_clave[("gin_or", n, longitud)]
                consultas = sorted(a)
                xa = [a[c].metrica(metrica) for c in consultas]
                xb = [b[c].metrica(metrica) for c in consultas]
                resultado[(tecnica, n, longitud)] = (
                    (sum(xa) - sum(xb)) / len(consultas),
                    prueba_pareada(xa, xb, PERMUTACIONES),
                )
    return resultado


def _num(valor: float) -> str:
    """Miles con espacio desde 100; si no, un decimal con coma."""
    return _miles(valor) if valor >= 100 else f"{valor:.1f}".replace(".", ",")


def _ms(valor: float) -> str:
    """Milisegundos: enteros con espacio desde 1 000; si no, como _fmt."""
    return _miles(valor) if valor >= 1_000 else _fmt(valor)


def _p(valor: float) -> str:
    return "< 0,001" if valor < 0.001 else _fmt(valor)


def tablas(datos) -> str:
    resumen = datos["resumen"]
    relevancia = datos["relevancia"]
    n_max = TAMANOS[-1]
    lineas = [
        "<!-- generado por generar_graficas_texto.py -->",
        "",
        "### Tiempo por consulta (ms, top-10)",
        "",
        "| Palabras | N | TF-IDF | BM25 | GIN OR | GIN AND | GIN OR / BM25 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for longitud in LONGITUDES:
        for n in TAMANOS:
            t = {x: ms_por_consulta(resumen[(caso_consulta(x, longitud), n)]) for x in SERIES}
            lineas.append(
                f"| {longitud} | {_miles(n)} | {_fmt(t['tf_idf'])} | {_fmt(t['bm25'])} | "
                f"{_fmt(t['gin_or'])} | {_fmt(t['gin_and'])} | {_fmt(t['gin_or'] / t['bm25'])} |"
            )

    lineas += [
        "",
        f"### Datos accedidos y documentos coincidentes por consulta (N = {_miles(n_max)})",
        "",
        (
            "| Palabras | KiB QuipuDB | KiB GIN OR | KiB GIN AND | Coincidentes QuipuDB "
            "| Coincidentes GIN OR | Coincidentes GIN AND |"
        ),
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for longitud in LONGITUDES:
        kib = {
            x: kib_por_consulta(resumen[(caso_consulta(x, longitud), n_max)], x)
            for x in ("bm25", "gin_or", "gin_and")
        }
        lineas.append(
            f"| {longitud} | {_miles(kib['bm25'])} | {_miles(kib['gin_or'])} | "
            f"{_miles(kib['gin_and'])} | {_num(coincidentes(datos, 'bm25', longitud, n_max))} | "
            f"{_num(coincidentes(datos, 'gin_or', longitud, n_max))} | "
            f"{_num(coincidentes(datos, 'gin_and', longitud, n_max))} |"
        )

    lineas += [
        "",
        "### Construcción y espacio",
        "",
        (
            "| N | SPIMI ms | GIN ms | to_tsvector ms | Heap QuipuDB KiB | Índice QuipuDB KiB "
            "| Tabla PostgreSQL KiB | de ella tsvector KiB | GIN KiB |"
        ),
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for n in TAMANOS:
        spimi = resumen[("spimi_construccion_indice", n)]
        gin = resumen[("gin_construccion_indice", n)]
        tokenizacion = resumen[("gin_tokenizacion", n)]
        tsvector = float(datos["entornos"]["174"][f"tabla.{n}.tsvector_bytes"])
        lineas.append(
            f"| {_miles(n)} | {_ms(float(spimi['mediana_tiempo_ns']) / 1e6)} | "
            f"{_ms(float(gin['mediana_tiempo_ns']) / 1e6)} | "
            f"{_ms(float(tokenizacion['mediana_tiempo_ns']) / 1e6)} | "
            f"{_miles(float(spimi['mediana_datos_bytes']) / 1024)} | "
            f"{_miles(float(spimi['mediana_indices_bytes']) / 1024)} | "
            f"{_miles(float(gin['mediana_datos_bytes']) / 1024)} | {_miles(tsvector / 1024)} | "
            f"{_miles(float(gin['mediana_indices_bytes']) / 1024)} |"
        )

    for metrica, titulo in (
        ("precision_10_empates", "Precision@10 (techo 0,161)"),
        ("recall_10_empates", "Recall@10"),
    ):
        lineas += [
            "",
            f"### {titulo}, empates promediados",
            "",
            "| Palabras | N | TF-IDF | BM25 | GIN OR | GIN AND |",
            "|---|---:|---:|---:|---:|---:|",
        ]
        for longitud in LONGITUDES:
            for n in TAMANOS:
                v = {x: relevancia[(x, n, longitud)][metrica] for x in SERIES}
                lineas.append(
                    f"| {longitud} | {_miles(n)} | {_fmt(v['tf_idf'])} | {_fmt(v['bm25'])} | "
                    f"{_fmt(v['gin_or'])} | {_fmt(v['gin_and'])} |"
                )

    pruebas = pareadas(datos)
    lineas += [
        "",
        "### Recall@10 de QuipuDB menos el de GIN OR, prueba pareada",
        "",
        "| Palabras | N | TF-IDF - GIN OR | p | BM25 - GIN OR | p |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for longitud in LONGITUDES:
        for n in TAMANOS:
            dt, pt = pruebas[("tf_idf", n, longitud)]
            db, pb = pruebas[("bm25", n, longitud)]
            lineas.append(
                f"| {longitud} | {_miles(n)} | {dt:+.3f} | {_p(pt)} | {db:+.3f} | {_p(pb)} |".replace(
                    ".", ","
                )
            )
    return "\n".join(lineas) + "\n"


FIGURAS = (
    ("texto_consultas", figura_consultas),
    ("texto_datos_accedidos", figura_datos_accedidos),
    ("texto_construccion", figura_construccion),
    ("texto_espacio", figura_espacio),
    ("texto_relevancia", figura_relevancia),
)


def generar(
    entrada: Path, datasets: Path, salida: Path, manifiesto: Path, informe: Path
) -> list[Path]:
    datos = cargar(entrada, datasets, manifiesto)
    texto = informe.read_text(encoding="utf-8")
    if texto.count(INICIO) != 1 or texto.count(FIN) != 1:
        raise ValueError(f"{informe} debe tener un {INICIO} y un {FIN}")
    bloque = tablas(datos)

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    salida.mkdir(parents=True, exist_ok=True)
    escritas = []
    with plt.rc_context({"font.family": "DejaVu Sans", "svg.hashsalt": "quipudb-issue174"}):
        for nombre, construir in FIGURAS:
            fig = construir(plt, datos)
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
    parser.add_argument("--datasets", type=Path, default=DATASETS)
    parser.add_argument("--salida", type=Path, default=SALIDA)
    parser.add_argument("--manifiesto", type=Path, default=MANIFIESTO)
    parser.add_argument("--informe", type=Path, default=INFORME)
    args = parser.parse_args()
    try:
        escritas = generar(args.entrada, args.datasets, args.salida, args.manifiesto, args.informe)
    except (OSError, ValueError, KeyError) as error:
        parser.exit(1, f"error: {error}\n")
    print(f"{len(escritas)} archivos en {args.salida}; tablas en {args.informe}")


if __name__ == "__main__":
    main()
