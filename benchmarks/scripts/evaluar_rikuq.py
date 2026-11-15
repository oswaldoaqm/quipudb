"""Evaluacion de Rikuq, el reconocimiento facial (issue #306).

Mide dos cosas que suelen confundirse y tiran en direcciones opuestas:

- **Que tan bien reconoce**: precision sobre un conjunto de prueba separado
  del que se cargo, y falsos positivos sobre rostros que no estan en la base.
- **Que tan rapido**: la latencia, reportada en **dos partes**. La busqueda en
  el motor es la que el enunciado acota a 100 ms; la vuelta completa incluye
  ademas detectar el rostro y sacar su vector, que es lo que de verdad espera
  quien usa la camara. Son numeros distintos y mezclarlos no dice nada. Es la
  misma separacion que reporta el informe del RAG con su "5 ms (motor 1 ms)".

Se corre desde el servidor de Rikuq::

    cd rikuq/server
    python ../../benchmarks/scripts/evaluar_rikuq.py medir

Necesita QuipuDB levantado con el dataset cargado (#302) y los modelos
bajados (``python -m app.rostros.modelos``).

Las cifras son de una maquina concreta y no de un servidor: se declaran como
locales en el informe y no se comparan contra numeros publicados de otros
sistemas.
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
RESULTADOS = RAIZ / "benchmarks" / "results"

# El enunciado: "optimiza para busquedas en menos de 100 ms".
LIMITE_MS = 100.0

CALENTAMIENTO = 3
"""Consultas que se tiran antes de medir.

La primera consulta carga el indice y las tablas del catalogo; medirla
contamina la mediana con un costo que no se paga despues.
"""

REPETICIONES = 5
"""Veces que se repite cada consulta. Se reporta la mediana.

La mediana y no el promedio: una pausa del recolector de basura o del disco
mueve el promedio y no la mediana.
"""


@dataclass(frozen=True, slots=True)
class Medicion:
    """Como le fue a una consulta."""

    ruta: str
    esperado: str
    """El nombre que el manifiesto dice que es, o "" si es un intruso."""
    obtenido: str
    """Lo que respondio el sistema, o "" si dijo desconocido."""
    distancia: float
    ms_motor: float
    """Lo que tardo SIMILAR_TO en el motor. Es lo que el enunciado acota."""
    ms_total: float
    """Lo que tarda la vuelta completa: detectar, describir y buscar."""

    @property
    def es_intruso(self) -> bool:
        return self.esperado == ""

    @property
    def acierto(self) -> bool:
        return not self.es_intruso and self.obtenido == self.esperado

    @property
    def falso_positivo(self) -> bool:
        return self.es_intruso and self.obtenido != ""


def _mediana(valores: list[float]) -> float:
    return statistics.median(valores) if valores else 0.0


def resumir(mediciones: list[Medicion]) -> dict[str, float]:
    """Las cifras que van al informe."""

    conocidos = [m for m in mediciones if not m.es_intruso]
    intrusos = [m for m in mediciones if m.es_intruso]
    aciertos = sum(1 for m in conocidos if m.acierto)
    rechazados = sum(1 for m in conocidos if m.obtenido == "")
    falsos = sum(1 for m in intrusos if m.falso_positivo)

    return {
        "consultas": len(conocidos),
        "aciertos": aciertos,
        "precision": aciertos / len(conocidos) if conocidos else 0.0,
        "rechazados": rechazados,
        "confundidos": len(conocidos) - aciertos - rechazados,
        "intrusos": len(intrusos),
        "falsos_positivos": falsos,
        "tasa_falsos_positivos": falsos / len(intrusos) if intrusos else 0.0,
        "ms_motor_mediana": _mediana([m.ms_motor for m in mediciones]),
        "ms_motor_p95": _percentil([m.ms_motor for m in mediciones], 95),
        "ms_total_mediana": _mediana([m.ms_total for m in mediciones]),
        "ms_total_p95": _percentil([m.ms_total for m in mediciones], 95),
    }


def _percentil(valores: list[float], cual: int) -> float:
    """El percentil sin interpolar, que con pocas muestras miente menos."""

    if not valores:
        return 0.0
    ordenados = sorted(valores)
    indice = min(len(ordenados) - 1, round(cual / 100 * len(ordenados)) - 1)
    return ordenados[max(0, indice)]


def _preparar_importaciones() -> None:
    """Deja importable el paquete ``app`` del servidor de Rikuq.

    El script vive en ``benchmarks/`` y el codigo en ``rikuq/server``; se
    corre desde ahi, pero asi funciona tambien desde la raiz.
    """

    ruta = str(RAIZ / "rikuq" / "server")
    if ruta not in sys.path:
        sys.path.insert(0, ruta)


def paso_medir(identidades: int, repeticiones: int) -> Path:
    """Corre el reconocimiento contra el motor y guarda cada consulta."""

    _preparar_importaciones()
    from app.config import Settings
    from app.consulta.identificar import UMBRAL_POR_DEFECTO, consulta_sql, identificar
    from app.consulta.umbral import acotar, repartir
    from app.dataset.lfw import DATOS, leer
    from app.motor import ClienteMotor
    from app.rostros.deteccion import crear_detector
    from app.rostros.embedding import crear_descriptor, vectores_de
    from app.rostros.errores import FeatureExtractionError

    rostros = acotar(leer(), identidades)
    detector, descriptor = crear_detector(), crear_descriptor()

    print(f"sacando vectores de {len(rostros)} imagenes...")
    vectores: dict[str, object] = {}
    for rostro in rostros:
        try:
            vectores[rostro.ruta] = vectores_de(rostro.archivo(DATOS), detector, descriptor)[0]
        except FeatureExtractionError:
            continue

    reparto = repartir(rostros, vectores, identidades=identidades)
    casos = [(persona, v) for persona, v in reparto.consultas]
    casos += [("", v) for _, v in reparto.intrusos]
    print(f"{len(reparto.consultas)} consultas y {len(reparto.intrusos)} intrusos")

    mediciones: list[Medicion] = []
    with ClienteMotor.desde(Settings.del_entorno()) as cliente:
        for _ in range(CALENTAMIENTO):
            cliente.query(consulta_sql(casos[0][1]))

        for numero, (esperado, vector) in enumerate(casos, start=1):
            if numero % 50 == 0:
                print(f"  {numero}/{len(casos)}")
            motores, totales = [], []
            respuesta = None
            for _ in range(repeticiones):
                inicio_total = time.perf_counter()
                inicio_motor = time.perf_counter()
                cliente.query(consulta_sql(vector))
                motores.append((time.perf_counter() - inicio_motor) * 1000)
                respuesta = identificar(cliente, vector, umbral=UMBRAL_POR_DEFECTO)
                totales.append((time.perf_counter() - inicio_total) * 1000)

            mediciones.append(
                Medicion(
                    ruta="",
                    esperado=esperado,
                    obtenido=respuesta.persona or "",
                    distancia=respuesta.distancia,
                    ms_motor=_mediana(motores),
                    ms_total=_mediana(totales),
                )
            )

    RESULTADOS.mkdir(parents=True, exist_ok=True)
    sello = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    destino = RESULTADOS / f"{sello}_rikuq_evaluacion.csv"
    with destino.open("w", encoding="utf-8", newline="") as archivo:
        escritor = csv.DictWriter(archivo, fieldnames=list(asdict(mediciones[0])))
        escritor.writeheader()
        for medicion in mediciones:
            escritor.writerow(asdict(medicion))

    resumen = resumir(mediciones)
    (RESULTADOS / f"{sello}_rikuq_resumen.json").write_text(
        json.dumps(resumen, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(tabla_resumen(resumen))
    print(f"\n{destino}")
    return destino


def tabla_resumen(resumen: dict[str, float]) -> str:
    cumple = "si" if resumen["ms_motor_mediana"] < LIMITE_MS else "NO"
    aciertos = f"{int(resumen['aciertos'])}/{int(resumen['consultas'])}"
    falsos = f"{int(resumen['falsos_positivos'])}/{int(resumen['intrusos'])}"
    tasa = f"{resumen['tasa_falsos_positivos']:.1%}"
    motor = f"{resumen['ms_motor_mediana']:.2f} ms (p95 {resumen['ms_motor_p95']:.2f} ms)"
    total = f"{resumen['ms_total_mediana']:.2f} ms (p95 {resumen['ms_total_p95']:.2f} ms)"
    return "\n".join(
        [
            f"precision            {resumen['precision']:.1%} ({aciertos})",
            f"rechazados           {int(resumen['rechazados'])}",
            f"confundidos          {int(resumen['confundidos'])}",
            f"falsos positivos     {falsos} ({tasa})",
            f"busqueda en el motor {motor} -- cumple los {LIMITE_MS:.0f} ms: {cumple}",
            f"vuelta completa      {total}",
        ]
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    sub = parser.add_subparsers(dest="paso", required=True)
    medir = sub.add_parser("medir", help="corre el reconocimiento contra el motor")
    medir.add_argument("--identidades", type=int, default=40)
    medir.add_argument("--repeticiones", type=int, default=REPETICIONES)
    args = parser.parse_args(argv)
    paso_medir(args.identidades, args.repeticiones)
    return 0


if __name__ == "__main__":
    sys.exit(main())
