"""Presentacion de resultados oficiales #39/#40; nunca ejecuta benchmarks."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from statistics import median

if __package__:
    from .banco_pruebas import CABECERA_ENTORNO, CABECERA_MEDICIONES, CABECERA_RESUMEN, METRICAS
else:
    from banco_pruebas import CABECERA_ENTORNO, CABECERA_MEDICIONES, CABECERA_RESUMEN, METRICAS

RAIZ = Path(__file__).resolve().parents[2]
TAMANOS = (1000, 10000, 100000)
IDS = {
    "39": "20260918T025045Z_530093b2675c4ff8a483020858915b56",
    "40": "20260918T035512Z_4e75ba53c6a6497798a0d95a278f9dfe",
}
ETIQUETAS = {
    "heap": "Heap",
    "sequential": "Secuencial",
    "bplus_clustered": "B+ agrupado",
    "bplus_unclustered": "B+ no agrupado",
    "extendible_hash": "Hash extensible",
}
COLORES = dict(zip(ETIQUETAS, ("#0072B2", "#D55E00", "#0072B2", "#D55E00", "#009E73")))
MARCADORES = dict(zip(ETIQUETAS, ("o", "s", "o", "s", "^")))
INICIO = "<!-- BEGIN RESULTADOS -->"
FIN = "<!-- END RESULTADOS -->"


@dataclass(frozen=True)
class Figura:
    issue: str
    operacion: str
    titulo: str
    detalle: str
    tecnicas: tuple[str, ...]

    @property
    def nombre(self):
        return f"{self.issue}_{self.operacion}"


ARCHIVOS = ("heap", "sequential")
INDICES = ("bplus_clustered", "bplus_unclustered", "extendible_hash")
FIGURAS = (
    Figura("39", "insercion", "Inserción inicial", "N inserciones + flush", ARCHIVOS),
    Figura("39", "busqueda_pk", "Búsqueda por clave primaria", "1000 consultas exitosas", ARCHIVOS),
    Figura(
        "39",
        "reorganizacion_explicita",
        "Reorganización explícita",
        "30% eliminado antes de medir; reorganize + flush",
        ("sequential",),
    ),
    Figura(
        "39",
        "eliminacion_con_reorganizacion",
        "Eliminación disparadora",
        "Una eliminación supera el 30%; incluye reorganización + flush",
        ("sequential",),
    ),
    Figura("40", "carga_indexada", "Carga indexada inicial", "N altas lógicas + flush", INDICES),
    Figura(
        "40",
        "construccion_indice",
        "Construcción de índice secundario",
        "create_index + flush sobre Heap previamente cargado",
        INDICES[1:],
    ),
    Figura(
        "40",
        "busqueda_igualdad",
        "Búsqueda por igualdad",
        "1000 consultas; recuperación de registros completos",
        INDICES,
    ),
    Figura(
        "40",
        "busqueda_rango",
        "Búsqueda por rango",
        "100 rangos; cada uno devuelve el 1% de N",
        INDICES[:2],
    ),
    Figura(
        "40",
        "recorrido_ordenado",
        "Recorrido ordenado",
        "Un recorrido completo; N registros recuperados",
        INDICES[:2],
    ),
    Figura(
        "40",
        "insercion_incremental",
        "Inserción incremental",
        "D = N/10 altas de claves nuevas mayores que N + flush",
        INDICES,
    ),
    Figura(
        "40",
        "eliminacion_incremental",
        "Eliminación incremental",
        "D = N/10 bajas + flush; estado final N − D",
        INDICES,
    ),
    Figura(
        "40",
        "mantenimiento_mixto",
        "Mantenimiento mixto",
        "3 ciclos de D bajas y D reinserciones; 6D operaciones + flush",
        INDICES,
    ),
)


def ns_a_ms(valor):
    return float(valor) / 1_000_000


def bytes_a_mib(valor):
    return float(valor) / 1_048_576


def numero(valor):
    try:
        resultado = float(valor)
    except (ValueError, TypeError) as error:
        raise ValueError(f"valor no numerico: {valor!r}") from error
    if not math.isfinite(resultado) or resultado < 0:
        raise ValueError(f"metrica no finita o negativa: {valor!r}")
    return resultado


def operaciones(operacion, n):
    if operacion in ("insercion", "carga_indexada"):
        return n
    if operacion in ("busqueda_pk", "busqueda_igualdad"):
        return 1000
    if operacion == "busqueda_rango":
        return 100
    if operacion in ("insercion_incremental", "eliminacion_incremental"):
        return n // 10
    if operacion == "mantenimiento_mixto":
        return 6 * (n // 10)
    return 1


def leer_csv(contenido, cabecera):
    try:
        lector = csv.DictReader(io.StringIO(contenido.decode("utf-8")), strict=True)
        if lector.fieldnames != list(cabecera):
            raise ValueError("cabecera CSV incorrecta")
        filas = list(lector)
        if not filas or any(None in fila or None in fila.values() for fila in filas):
            raise ValueError("CSV vacio o fila con columnas incorrectas")
        return filas
    except (UnicodeError, csv.Error) as error:
        raise ValueError(f"CSV invalido: {error}") from error


def validar_corrida(issue, datos):
    """Audita grupos completos y recalcula resumenes sin alterar las muestras."""
    esperado = {
        (f"{tecnica}_{fig.operacion}", n): (tecnica, fig.operacion)
        for fig in FIGURAS
        if fig.issue == issue
        for tecnica in fig.tecnicas
        for n in TAMANOS
    }
    for filas in datos.values():
        if any(fila["ejecucion"] != IDS[issue] for fila in filas):
            raise ValueError("ejecucion distinta de la oficial")
    entorno = {}
    for fila in datos["entorno"]:
        if fila["clave"] in entorno:
            raise ValueError("clave de entorno duplicada")
        entorno[fila["clave"]] = fila["valor"]
    for clave, valor in {
        "calentamientos": "1",
        "repeticiones": "5",
        "declarado.proposito": f"experimento_oficial_issue_{issue}",
    }.items():
        if entorno.get(clave) != valor:
            raise ValueError(f"configuracion oficial incorrecta: {clave}")

    grupos = defaultdict(list)
    resumenes = {}
    for tipo in ("mediciones", "resumen"):
        for fila in datos[tipo]:
            clave = (fila["caso"], int(fila["n_registros"]))
            if clave not in esperado:
                raise ValueError(f"caso/tamano no soportado: {clave}")
            tecnica, operacion = esperado[clave]
            if (fila["tecnica"], fila["operacion"]) != (tecnica, operacion):
                raise ValueError("tecnica/operacion incorrecta")
            if int(fila["n_operaciones"]) != operaciones(operacion, clave[1]):
                raise ValueError("cantidad de operaciones incorrecta")
            if tipo == "mediciones":
                if fila["estado"] != "ok":
                    raise ValueError("medicion con estado distinto de ok")
                for metrica in METRICAS:
                    numero(fila[metrica])
                if numero(fila["tiempo_ns"]) == 0:
                    raise ValueError("tiempo debe ser positivo")
                if numero(fila["datos_bytes"]) + numero(fila["indices_bytes"]) != numero(
                    fila["espacio_despues_bytes"]
                ):
                    raise ValueError("espacio total inconsistente")
                grupos[clave].append(fila)
            else:
                if clave in resumenes:
                    raise ValueError("resumen duplicado")
                resumenes[clave] = fila
    if set(grupos) != set(esperado) or set(resumenes) != set(esperado):
        raise ValueError("casos/tamanos incompletos")
    for clave, muestras in grupos.items():
        if sorted(int(f["repeticion"]) for f in muestras) != [1, 2, 3, 4, 5]:
            raise ValueError("se requieren cinco repeticiones unicas 1..5")
        resumen = resumenes[clave]
        if int(resumen["repeticiones"]) != 5:
            raise ValueError("resumen sin cinco repeticiones")
        for metrica in METRICAS:
            if numero(resumen[f"mediana_{metrica}"]) != median(
                numero(f[metrica]) for f in muestras
            ):
                raise ValueError(f"mediana inconsistente: {clave}, {metrica}")
        tiempos = [numero(f["tiempo_ns"]) for f in muestras]
        if (numero(resumen["min_tiempo_ns"]), numero(resumen["max_tiempo_ns"])) != (
            min(tiempos),
            max(tiempos),
        ):
            raise ValueError("minimo/maximo inconsistente")
    return resumenes, grupos


def cargar_fuentes(entrada, manifiesto):
    """Solo lee los seis nombres exactos y exige los hashes del manifiesto."""
    try:
        fuentes = json.loads(manifiesto.read_text(encoding="utf-8"))
        if set(fuentes["corridas"]) != set(IDS):
            raise ValueError("se requieren exactamente las corridas #39 y #40")
        resultado = {}
        cabeceras = {
            "mediciones": CABECERA_MEDICIONES,
            "resumen": CABECERA_RESUMEN,
            "entorno": CABECERA_ENTORNO,
        }
        for issue, ident in IDS.items():
            fuente = fuentes["corridas"][issue]
            if fuente["ejecucion"] != ident or set(fuente["archivos"]) != set(cabeceras):
                raise ValueError("manifiesto de corrida incorrecto")
            datos = {}
            for tipo, cabecera in cabeceras.items():
                archivo = fuente["archivos"][tipo]
                nombre = f"{ident}_{tipo}.csv"
                if archivo["nombre"] != nombre:
                    raise ValueError("nombre de fuente distinto del oficial")
                contenido = (entrada / nombre).read_bytes()
                if hashlib.sha256(contenido).hexdigest() != archivo["sha256"]:
                    raise ValueError(f"SHA-256 distinto: {nombre}")
                datos[tipo] = leer_csv(contenido, cabecera)
            resumenes, grupos = validar_corrida(issue, datos)
            resultado[issue] = (resumenes, grupos)
        return resultado
    except (OSError, KeyError, json.JSONDecodeError) as error:
        raise ValueError(f"fuentes oficiales inexistentes o invalidas: {error}") from error


def serie(resumenes, tecnica, operacion, campo="mediana_tiempo_ns"):
    return [numero(resumenes[(f"{tecnica}_{operacion}", n)][campo]) for n in TAMANOS]


def guardar_figura(fig, carpeta, nombre):
    for formato in ("png", "svg"):
        metadata = {"Software": "QuipuDB issue #41"} if formato == "png" else {"Date": None}
        ruta = carpeta / f"{nombre}.{formato}"
        if formato == "svg":
            buffer = io.StringIO()
            fig.savefig(buffer, format="svg", metadata=metadata)
            # Matplotlib agrega espacios finales en atributos path; el salto de
            # linea sigue separando coordenadas tras normalizar ese whitespace.
            texto = "\n".join(linea.rstrip() for linea in buffer.getvalue().splitlines()) + "\n"
            ruta.write_text(texto, encoding="utf-8")
        else:
            fig.savefig(ruta, dpi=300, metadata=metadata)


def estilo_eje(ax, titulo, detalle, ylabel):
    ax.set_title(f"{titulo}\n{detalle}", fontsize=12, pad=14)
    ax.set_xlabel("N inicial (registros) · escala logarítmica")
    ax.set_ylabel(ylabel)
    ax.set_xscale("log")
    ax.set_xticks(TAMANOS, ("1 000", "10 000", "100 000"))
    ax.minorticks_off()
    ax.grid(True, alpha=0.22)


def graficar_tiempo(plt, figura, resumenes, grupos):
    fig, ax = plt.subplots(figsize=(9, 5.6), layout="constrained")
    for tecnica in figura.tecnicas:
        valores = [ns_a_ms(v) for v in serie(resumenes, tecnica, figura.operacion)]
        minimos = [ns_a_ms(v) for v in serie(resumenes, tecnica, figura.operacion, "min_tiempo_ns")]
        maximos = [ns_a_ms(v) for v in serie(resumenes, tecnica, figura.operacion, "max_tiempo_ns")]
        ax.errorbar(
            TAMANOS,
            valores,
            yerr=(
                [v - m for v, m in zip(valores, minimos)],
                [m - v for v, m in zip(valores, maximos)],
            ),
            label=ETIQUETAS[tecnica],
            color=COLORES[tecnica],
            marker=MARCADORES[tecnica],
            capsize=5,
            linewidth=1.7,
        )
        for n in TAMANOS:
            muestras = grupos[(f"{tecnica}_{figura.operacion}", n)]
            ax.scatter(
                [n] * len(muestras),
                [ns_a_ms(f["tiempo_ns"]) for f in muestras],
                color=COLORES[tecnica],
                alpha=0.45,
                s=15,
                zorder=3,
            )
    # Una escala uniforme positiva mantiene visibles tanto colas como tecnicas rapidas.
    ax.set_yscale("log")
    estilo_eje(
        ax,
        f"#{figura.issue} · {figura.titulo}",
        figura.detalle,
        "Tiempo del lote / operación (ms) · escala logarítmica",
    )
    if figura.operacion == "busqueda_igualdad":
        from matplotlib.ticker import FuncFormatter, LogLocator

        ax.yaxis.set_major_locator(LogLocator(base=10, subs=(1, 2, 3, 5)))
        ax.yaxis.set_major_formatter(FuncFormatter(lambda valor, _: f"{valor:g}"))
    ax.legend(loc="best")
    fig.supxlabel(
        "Mediana y mínimo–máximo de 5 repeticiones; puntos: todas las muestras.\n"
        "1 calentamiento excluido · sin descartar valores altos",
        fontsize=9,
    )
    return fig


def graficar_espacio(plt, issue, resumenes, incremental=False):
    from matplotlib.patches import Patch

    tecnicas = ARCHIVOS if issue == "39" else INDICES
    op = "insercion" if issue == "39" else "carga_indexada"
    fig, axes = plt.subplots(1, 3, figsize=(12, 5), layout="constrained")
    for ax, n in zip(axes, TAMANOS):
        for x, tecnica in enumerate(tecnicas):
            fila = resumenes[(f"{tecnica}_{op}", n)]
            if incremental:
                fila = resumenes[(f"{tecnica}_insercion_incremental", n)]
                ax.bar(
                    x - 0.18,
                    bytes_a_mib(fila["mediana_espacio_antes_bytes"]),
                    0.36,
                    color=COLORES[tecnica],
                    alpha=0.45,
                    label="Antes: N" if x == 0 else None,
                )
                ax.bar(
                    x + 0.18,
                    bytes_a_mib(fila["mediana_espacio_despues_bytes"]),
                    0.36,
                    color=COLORES[tecnica],
                    label="Después: N + D" if x == 0 else None,
                )
            else:
                datos = bytes_a_mib(fila["mediana_datos_bytes"])
                ax.bar(
                    x,
                    datos,
                    0.6,
                    color=COLORES[tecnica],
                    label="Archivo de datos*" if x == 0 else None,
                )
                if issue == "40":
                    ax.bar(
                        x,
                        bytes_a_mib(fila["mediana_indices_bytes"]),
                        0.6,
                        bottom=datos,
                        color=COLORES[tecnica],
                        hatch="///",
                        edgecolor="black",
                        linewidth=0.4,
                        label="Índice secundario" if x == 0 else None,
                    )
        ax.set_title(f"N = {n:,}".replace(",", " "))
        ax.set_xticks(
            range(len(tecnicas)), [ETIQUETAS[t].replace(" ", "\n", 1) for t in tecnicas], fontsize=9
        )
        ax.set_ylabel("Tamaño de archivos (MiB)")
        maximo = max(barra.get_y() + barra.get_height() for barra in ax.patches)
        ax.set_ylim(0, maximo * 1.25)
        ax.grid(axis="y", alpha=0.2)
        ax.set_axisbelow(True)
    if issue == "40":
        if incremental:
            leyenda = [
                Patch(facecolor="gray", alpha=0.45, label="Antes: N"),
                Patch(facecolor="gray", label="Después: N + D"),
            ]
        else:
            leyenda = [
                Patch(facecolor="gray", label="Archivo de datos*"),
                Patch(facecolor="gray", hatch="///", edgecolor="black", label="Índice secundario"),
            ]
        axes[-1].legend(handles=leyenda, fontsize=8, loc="upper right")
    titulo = "Espacio antes y después de D = N/10 altas" if incremental else "Espacio tras carga"
    fig.suptitle(f"#{issue} · {titulo}\nEscala vertical independiente por panel", fontsize=13)
    pie = "Archivos reales tras flush · 1 MiB = 1 048 576 bytes · medianas de 5 repeticiones"
    if issue == "40":
        pie += "\n* En B+ agrupado, datos incluye el árbol integrado; no hay archivo secundario."
    fig.supxlabel(pie, fontsize=9)
    return fig


def tablas_resultados(corridas):
    partes = []
    for issue, (resumenes, grupos) in corridas.items():
        partes += [
            f"### Resultados oficiales #{issue}",
            "",
            (
                f"Fuente: `{IDS[issue]}`. {len(grupos) * 5} muestras; "
                f"{len(resumenes)} resúmenes. Tiempos en ms por lote/operación."
            ),
            "",
            "| Caso | 1k (ms) | 10k (ms) | 100k (ms) |",
            "|---|---:|---:|---:|",
        ]
        for figura in FIGURAS:
            if figura.issue != issue:
                continue
            for tecnica in figura.tecnicas:
                valores = serie(resumenes, tecnica, figura.operacion)
                partes.append(
                    f"| {tecnica}_{figura.operacion} | "
                    + " | ".join(f"{ns_a_ms(v):.6f}" for v in valores)
                    + " |"
                )
        partes += [
            "",
            "#### Espacio de archivos (bytes)",
            "",
            (
                "| N inicial | Técnica | Datos tras carga | Índice secundario | Total tras carga |"
                " Total tras altas incrementales |"
            ),
            "|---:|---|---:|---:|---:|---:|",
        ]
        for n in TAMANOS:
            for tecnica in ARCHIVOS if issue == "39" else INDICES:
                op = "insercion" if issue == "39" else "carga_indexada"
                fila = resumenes[(f"{tecnica}_{op}", n)]
                valores = [
                    fila[f"mediana_{campo}"]
                    for campo in ("datos_bytes", "indices_bytes", "espacio_despues_bytes")
                ]
                despues = "No medido"
                if issue == "40":
                    despues = resumenes[(f"{tecnica}_insercion_incremental", n)][
                        "mediana_espacio_despues_bytes"
                    ]
                partes.append(
                    f"| {n} | {ETIQUETAS[tecnica]} | " + " | ".join(valores) + f" | {despues} |"
                )
        partes += [
            "",
            "#### Grupos con máximo ≥ 1,5 veces la mediana",
            "",
            (
                "Criterio descriptivo, no prueba estadística ni filtro. Todas las muestras "
                "se conservan; lista en orden de repetición."
            ),
            "",
        ]
        for (caso, n), fila in resumenes.items():
            if numero(fila["max_tiempo_ns"]) >= 1.5 * numero(fila["mediana_tiempo_ns"]):
                muestras = sorted(grupos[(caso, n)], key=lambda r: int(r["repeticion"]))
                partes.append(
                    f"- `{caso}`, N={n}: "
                    + ", ".join(f"{ns_a_ms(f['tiempo_ns']):.6f}" for f in muestras)
                    + " ms."
                )
        partes.append("")
    return "\n".join(partes)


def generar(entrada, salida, manifiesto, plantilla):
    """Valida antes de exportar; la salida no puede contener las fuentes CSV."""
    if entrada.resolve() == salida.resolve() or entrada.resolve().is_relative_to(salida.resolve()):
        raise ValueError("la salida no debe contener la carpeta de fuentes")
    corridas = cargar_fuentes(entrada, manifiesto)
    texto = plantilla.read_text(encoding="utf-8")
    if texto.count(INICIO) != 1 or texto.count(FIN) != 1 or texto.index(INICIO) > texto.index(FIN):
        raise ValueError("plantilla sin bloque unico de resultados")
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError as error:
        raise ValueError("se requiere Matplotlib, ya declarado en requirements.txt") from error
    carpeta = salida / "graficas"
    carpeta.mkdir(parents=True, exist_ok=True)
    with plt.rc_context({"font.family": "DejaVu Sans", "svg.hashsalt": "quipudb-issue41"}):
        for figura in FIGURAS:
            fig = graficar_tiempo(plt, figura, *corridas[figura.issue])
            try:
                guardar_figura(fig, carpeta, figura.nombre)
            finally:
                plt.close(fig)
        for issue, incremental in (("39", False), ("40", False), ("40", True)):
            fig = graficar_espacio(plt, issue, corridas[issue][0], incremental)
            nombre = f"{issue}_espacio_" + ("incremental" if incremental else "inicial")
            try:
                guardar_figura(fig, carpeta, nombre)
            finally:
                plt.close(fig)
    bloque = tablas_resultados(corridas)
    bloque += f"\nRenderizador: Matplotlib {matplotlib.__version__}; PNG 300 dpi y SVG.\n"
    informe = texto.split(INICIO)[0] + INICIO + "\n\n" + bloque + "\n" + FIN + texto.split(FIN)[1]
    (salida / "comparacion_experimental.md").write_text(informe, encoding="utf-8")
    return 2 * (len(FIGURAS) + 3)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--entrada", type=Path, default=RAIZ / "benchmarks/results")
    parser.add_argument("--salida", type=Path, default=RAIZ / "docs/informe")
    parser.add_argument(
        "--manifiesto", type=Path, default=RAIZ / "docs/informe/fuentes_resultados.json"
    )
    args = parser.parse_args(argv)
    try:
        cantidad = generar(
            args.entrada,
            args.salida,
            args.manifiesto,
            RAIZ / "docs/informe/comparacion_experimental.md",
        )
    except (ValueError, OSError) as error:
        parser.error(str(error))
    print(f"{cantidad} archivos graficos e informe generados; no se ejecutaron benchmarks.")


if __name__ == "__main__":
    main()
