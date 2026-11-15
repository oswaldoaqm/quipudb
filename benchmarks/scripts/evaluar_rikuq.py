"""Evaluacion de Rikuq, el reconocimiento facial (issue #306).

Mide dos cosas que suelen confundirse y tiran en direcciones opuestas:

- **Que tan bien reconoce**: precision sobre un conjunto de prueba separado
  del que se cargo, y falsos positivos sobre rostros que no estan en la base.
- **Que tan rapido**: la latencia, reportada en **dos partes**. La busqueda en
  el motor es la que el enunciado acota a 100 ms; la vuelta completa incluye
  ademas detectar el rostro y sacar su vector, que es lo que de verdad espera
  quien usa la camara. Son numeros distintos y mezclarlos no dice nada. Es la
  misma separacion que reporta el informe del RAG con su "5 ms (motor 1 ms)".

Las cifras son de una maquina concreta y no de un servidor: se declaran como
locales en el informe y no se comparan contra numeros publicados de otros
sistemas.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

# El enunciado: "optimiza para busquedas en menos de 100 ms".
LIMITE_MS = 100.0


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
