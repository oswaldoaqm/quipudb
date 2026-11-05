"""Exporta las 14 figuras #193 desde fuentes verificadas, sin medir indices."""

import argparse
import json
import sys
from pathlib import Path

import matplotlib

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from benchmarks.scripts.datos_parte4 import huellas, leer_191, leer_192
from benchmarks.scripts.graficas_parte4 import construccion, consultas, estilo, metricas, recall

RAIZ = Path(__file__).resolve().parents[2]


def generar(a, b, salida, manifiesto, registrar=False):
    actual = {"191": huellas(a), "192": huellas(b)}
    if not registrar:
        esperado = json.loads(manifiesto.read_text())
        diferencias = [
            f"#{issue}/{nombre}"
            for issue in ("191", "192")
            for nombre in sorted(actual[issue].keys() | esperado[issue].keys())
            if actual[issue].get(nombre) != esperado[issue].get(nombre)
        ]
        if diferencias:
            raise ValueError(
                "fuentes faltantes, agregadas o modificadas: " + ", ".join(diferencias[:10])
            )
    datos191, datos192 = leer_191(a), leer_192(b)
    if registrar:
        with manifiesto.open("x") as f:
            json.dump(actual, f, indent=2, sort_keys=True)
    salida.mkdir(parents=True, exist_ok=True)
    estilo()
    archivos = (
        construccion(datos191, salida)
        + consultas(datos191, salida)
        + recall(datos191, salida)
        + metricas(datos192, salida)
    )
    if actual != {"191": huellas(a), "192": huellas(b)}:
        raise ValueError("las fuentes cambiaron durante la exportacion")
    (salida / "renderizado.json").write_text(
        json.dumps(
            {
                "matplotlib": matplotlib.__version__,
                "dpi": 300,
                "fuente": "DejaVu Sans",
                "figuras": [p.name for p in archivos],
            },
            indent=2,
        )
        + "\n"
    )
    return archivos


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--ivf-hnsw", type=Path, default=RAIZ / "benchmarks/datasets/ivf_hnsw_191")
    p.add_argument("--metricas", type=Path, default=RAIZ / "benchmarks/datasets/metricas_192")
    p.add_argument("--salida", type=Path, default=RAIZ / "docs/informe/graficas/multimedia")
    p.add_argument("--manifiesto", type=Path, default=RAIZ / "docs/informe/fuentes_multimedia.json")
    p.add_argument(
        "--registrar-fuentes",
        action="store_true",
        help="solo alta inicial; manifiesto debe ser nuevo",
    )
    a = p.parse_args()
    if any(
        a.salida.resolve().is_relative_to(d.resolve())
        or d.resolve().is_relative_to(a.salida.resolve())
        for d in (a.ivf_hnsw, a.metricas)
    ):
        p.error("salida y fuentes deben estar separadas")
    archivos = generar(a.ivf_hnsw, a.metricas, a.salida, a.manifiesto, a.registrar_fuentes)
    print(f"{len(archivos)} figuras exportadas a {a.salida}")


if __name__ == "__main__":
    main()
