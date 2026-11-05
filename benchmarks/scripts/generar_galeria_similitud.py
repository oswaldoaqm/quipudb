"""Galeria #194: copia catalogos de #192, verifica consultas y exporta ejemplos."""

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from benchmarks.scripts.casos_galeria import SEMILLA, K, huella_resultados, preparar
from benchmarks.scripts.multimedia_dataset import sha256

RAIZ = Path(__file__).resolve().parents[2]


def figura(caso, dataset, salida):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.image as mpimg
    import matplotlib.pyplot as plt

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 11})
    fig, axes = plt.subplots(2, K + 1, figsize=(16, 8.4), layout="constrained")
    fig.get_layout_engine().set(h_pad=0.16, hspace=0.12)
    q = caso["consulta"]
    fig.suptitle(f"Consulta {q:06d} · Top 5 · Distancia coseno ↓", fontsize=19)
    umbral = caso["resultados"]["exacto"][-1]["distancia"]
    for fila, (tecnica, parametro) in enumerate((("ivf", "nprobe=16"), ("hnsw", "ef_search=80"))):
        for col, ax in enumerate(axes[fila]):
            consulta = col == 0
            r = {"nombre": caso["nombre"]} if consulta else caso["resultados"][tecnica][col - 1]
            path = dataset / "archivos" / r["nombre"]
            ax.imshow(mpimg.imread(path), cmap="gray", vmin=0, vmax=1, interpolation="nearest")
            ax.set_xticks([])
            ax.set_yticks([])
            color = "#2563a6" if consulta else "#248047" if r["distancia"] <= umbral else "#b13b31"
            for spine in ax.spines.values():
                spine.set_color(color)
                spine.set_linewidth(3)
            ax.set_title("CONSULTA" if consulta else f"{col}. {r['nombre']}", fontsize=12)
            ax.set_xlabel(r["nombre"] if consulta else f"d = {r['distancia']:.6f}", fontsize=12)
            if consulta:
                ev = caso["evaluacion"][tecnica]
                clase = "Éxito exacto" if ev["clase"] == "exito" else "Error aproximado"
                ax.set_title(f"CONSULTA · {tecnica.upper()}\n{parametro}", fontsize=12)
                ax.set_xlabel(
                    f"{r['nombre']}\n{clase} · R@5={ev['recall5_empates']:.2f}", fontsize=10
                )
    fig.supxlabel(
        "Azul: consulta · Verde: Top 5 exacto o empate · Rojo: fuera del umbral exacto\nSin etiquetas semánticas: verde no significa que represente el mismo objeto",
        fontsize=11,
    )
    path = salida / f"imagenes_{q:06d}.png"
    fig.savefig(path, dpi=180, metadata={"Software": "QuipuDB #194"})
    plt.close(fig)
    return path


def interpretar(caso):
    textos = []
    for t in ("ivf", "hnsw"):
        ev = caso["evaluacion"][t]
        texto = f"{t.upper()} recupera {ev['recall5'] * K:.0f} de los 5 RIDs exactos"
        if ev["clase"] == "error":
            texto += "; faltan " + ", ".join(f"{i:06d}" for i in ev["faltantes_exactos"])
        texto += f" (Recall@5 con empates: {ev['recall5_empates']:.2f})."
        textos.append(texto)
    mismos = [r["id"] for r in caso["resultados"]["ivf"]] == [
        r["id"] for r in caso["resultados"]["hnsw"]
    ]
    textos.append(
        "Ambos índices devuelven la misma lista ordenada."
        if mismos
        else "Las listas recuperadas por los índices son distintas."
    )
    if caso["evaluacion"]["ivf"]["clase"] == "error":
        textos.append(
            "Control: al ampliar IVF a nprobe=32 se recupera el Top 10 exacto, "
            "igual que en el CSV del #192. La omisión desaparece al ampliar la búsqueda."
        )
    return " ".join(textos)


def exportar(datos, datasets, salida, observaciones=None):
    """Solo publica una salida nueva y completa; nunca sustituye una galeria previa."""
    if salida.exists():
        raise ValueError("salida existente; use una carpeta nueva")
    for dataset in datasets.values():
        if salida.resolve().is_relative_to(dataset.resolve()) or dataset.resolve().is_relative_to(
            salida.resolve()
        ):
            raise ValueError("salida y datasets deben estar separados")
    notas = observaciones or {}
    salida.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".galeria-", dir=salida.parent) as temp:
        out = Path(temp) / "galeria"
        out.mkdir()
        lineas = [
            "# Galería de similitud multimedia (#194)",
            "",
            (
                "Se reutilizan los vectores TF-IDF de 64 componentes y los índices persistidos del #192, "
                "sin extraer rasgos ni construir índices. Las búsquedas se ejecutan en copias temporales "
                "de los catálogos y sus Top 10 se contrastan con los CSV originales; aquí se muestran los primeros cinco."
            ),
            "",
            "## Selección y lectura",
            "",
            (
                f"Corpus: 1 000 imágenes y 1 000 clips sintéticos de #190. Semilla: {datos[0]['semilla']}. "
                "Se ordenan las 100 consultas del #192 por la suma de coincidencias Top 5 de IVF y HNSW "
                "frente al exacto. Se eligen las dos menores, la mediana y la mayor; los empates se "
                "desempatan con un barajado reproducible. No se cambian parámetros para provocar errores."
            ),
            "",
            (
                "IVF: nprobe=16, nlist=32. HNSW: ef_search=80, M=16, ef_construction=200. "
                "Ambos usan COSINE, el mismo vocabulario, vectores y consultas. Se solicitan 11 candidatos, "
                "se excluye el propio ID y se conservan cinco. Todos los ejemplos son consultas del corpus."
            ),
            "",
            (
                "**d = 1 − similitud coseno**, distancia adimensional en [0, 2]; menor es más cercano. "
                "Los valores mostrados proceden del motor nativo, no se estiman desde las imágenes. "
                "Los decimales se redondean solo para presentar; resultados.json conserva el valor completo."
            ),
            "",
            (
                "**Éxito/error** indica recuperación del Top 5 exacto, admitiendo empates exactos en la "
                "distancia double como en #191. No significa acierto/error semántico: no hay clases ni "
                "etiquetas de objetos. La semejanza visual o auditiva queda como observación, no como verdad de referencia."
            ),
            "",
            "## Imágenes",
            "",
        ]
        for grupo in datos:
            modalidad = grupo["modalidad"]
            if modalidad == "audio":
                lineas += [
                    "## Audio sintético",
                    "",
                    (
                        "Son WAV PCM16 mono, 16 kHz y 0,5 s: tonos armónicos con barrido y modulación, no canciones. "
                        "Los enlaces permiten consultar los clips originales incluidos, sin depender del dataset ignorado. "
                        "MFCC y TF-IDF describen propiedades espectrales; no hay etiquetas musicales para evaluar relevancia."
                    ),
                    "",
                ]
                (out / "audio").mkdir()
            for caso in grupo["casos"]:
                q = caso["consulta"]
                lineas += [f"### Consulta {caso['nombre']}", ""]
                if modalidad == "imagenes":
                    fig = figura(caso, datasets[modalidad], out)
                    lineas += [f"![Consulta y vecinos {q:06d}]({fig.name})", ""]
                else:
                    nombres = {caso["nombre"]} | {
                        r["nombre"] for rs in caso["resultados"].values() for r in rs
                    }
                    for nombre in sorted(nombres):
                        src = datasets[modalidad] / "archivos" / nombre
                        dst = out / "audio" / nombre
                        if not dst.exists():
                            shutil.copyfile(src, dst)
                    lineas += [
                        f"[Escuchar consulta {caso['nombre']}](audio/{caso['nombre']})",
                        "",
                        "| Índice | Puesto | Archivo | Distancia coseno ↓ |",
                        "|---|---:|---|---:|",
                    ]
                    for tecnica in ("ivf", "hnsw"):
                        for puesto, r in enumerate(caso["resultados"][tecnica], 1):
                            lineas.append(
                                f"| {tecnica.upper()} | {puesto} | [{r['nombre']}](audio/{r['nombre']}) | {r['distancia']:.6f} |"
                            )
                    lineas += [""]
                lineas += [interpretar(caso), ""]
                clave = f"{modalidad}/{q}"
                if clave in notas:
                    nota = notas[clave]
                    src = datasets[modalidad] / "archivos" / caso["nombre"]
                    if nota["consulta_sha256"] != sha256(src):
                        raise ValueError("la observacion visual no corresponde a la consulta")
                    if nota["resultados_sha256"] != huella_resultados(caso):
                        raise ValueError("la observacion no corresponde a los resultados")
                    lineas += [nota["texto"], ""]
                exactos = ", ".join(
                    f"{r['nombre']} (d={r['distancia']:.6f})" for r in caso["resultados"]["exacto"]
                )
                lineas += ["Referencia exacta: " + exactos + ".", ""]
        lineas += [
            "## Límites e interpretación",
            "",
            (
                "SIFT trabaja en escala de grises; no se atribuyen errores al color. Las imágenes contienen "
                "círculos, anillos, segmentos y ruido. El histograma de palabras visuales no conserva las "
                "posiciones globales de esas formas, por lo que cercanía del vector no garantiza la misma composición."
            ),
            "",
            (
                "Los errores marcados en rojo son omisiones verificadas respecto al exacto. IVF visita 16 de "
                "32 listas; no se instrumentó qué lista explica cada omisión, así que no se afirma que un "
                "fondo o una textura sean su causa. Las observaciones visuales describen los ejemplos, no "
                "demuestran causalidad interna. No se han probado errores semánticos con etiquetas externas."
            ),
            "",
            (
                "No se encontraron errores de Top 5 en las consultas de audio seleccionadas con estos parámetros. "
                "No se fabrican errores ni títulos de canciones para completar la galería. La selección de extremos "
                "ilustra casos; no estima la frecuencia de fallos ni compara rendimiento."
            ),
            "",
            "## Procedencia y reproducción",
            "",
            (
                "Imágenes y audios sintéticos generados por generar_multimedia.py de QuipuDB; "
                "no se incorporan grabaciones de terceros. Código del repositorio bajo licencia MIT (véase "
                "[LICENSE](../../../LICENSE)). Los WAV se copian sin alterarlos y las imágenes se muestran en composiciones."
            ),
            "",
            (
                "[resultados.json](resultados.json) conserva IDs, RIDs, distancias completas, parámetros, "
                "evaluaciones y hashes de fuentes y recursos. Las consultas se recuperaron del #192/COSINE, "
                "repetición 0. Los comandos están en el [README de benchmarks](../../../benchmarks/README.md#galería-de-similitud-194). "
                "Los PNG y WAV incluidos bastan para consultar la galería desde GitHub. Para regenerarla se "
                "requieren las fuentes locales y el binding nativo; no se sustituyen por simulaciones."
            ),
            "",
        ]
        (out / "README.md").write_text("\n".join(lineas), encoding="utf-8")
        recursos = {
            str(p.relative_to(out)): sha256(p) for p in sorted(out.rglob("*")) if p.is_file()
        }
        import matplotlib

        (out / "resultados.json").write_text(
            json.dumps(
                {
                    "formato": 1,
                    "grupos": datos,
                    "observaciones": notas,
                    "renderizado": {"matplotlib": matplotlib.__version__, "dpi": 180},
                    "recursos": recursos,
                },
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        out.rename(salida)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--datasets", type=Path, default=RAIZ / "benchmarks/datasets/multimedia")
    p.add_argument("--resultados", type=Path, default=RAIZ / "benchmarks/datasets/metricas_192")
    p.add_argument("--salida", type=Path, default=RAIZ / "docs/informe/galeria_similitud")
    p.add_argument("--semilla", type=int, default=SEMILLA)
    p.add_argument("--observaciones", type=Path)
    args = p.parse_args()
    if args.salida.exists():
        p.error("salida existente; use una carpeta nueva")
    for fuente in (args.datasets, args.resultados):
        if args.salida.resolve().is_relative_to(
            fuente.resolve()
        ) or fuente.resolve().is_relative_to(args.salida.resolve()):
            p.error("salida y fuentes deben estar separadas")
    datasets = {m: args.datasets / f"{m}_1000" for m in ("imagenes", "audio")}
    datos = [
        preparar(datasets[m], args.resultados / f"{m}_1000/COSINE", args.semilla) for m in datasets
    ]
    notas = (
        json.loads(args.observaciones.read_text(encoding="utf-8")) if args.observaciones else None
    )
    exportar(datos, datasets, args.salida, notas)
    print(f"Galeria exportada a {args.salida}")


if __name__ == "__main__":
    main()
