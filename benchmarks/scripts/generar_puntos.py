"""Genera los CSV de puntos geograficos de 1k, 10k y 100k para el 2.2.4 (issue #130).

Los puntos NO son uniformes sobre el mundo: se concentran alrededor de las
quince ciudades mas pobladas del Peru, con una cantidad proporcional a su
poblacion y una dispersion que crece con el tamano de la ciudad. Con puntos
repartidos al azar por el planeta los MBR del R-Tree casi no se solaparian y
el indice saldria favorecido de forma artificial; datos concentrados en
ciudades, que es el caso real, son mas exigentes.
"""

from __future__ import annotations

import argparse
import csv
import math
import random
from dataclasses import dataclass
from pathlib import Path

SEMILLA = 20260906
TAMANOS = (1_000, 10_000, 100_000)
CABECERA = ("id", "ciudad", "latitud", "longitud")
SALIDA_PREDETERMINADA = Path(__file__).resolve().parents[1] / "datasets"

# Kilometros por grado de latitud con el radio medio de la Tierra (6 371,0088 km),
# el mismo que usa la Haversine del core.
KM_POR_GRADO = 111.195
# Desviacion de una ciudad de un millon de habitantes. Crece con la raiz de la
# poblacion, porque la poblacion crece con el area y no con el radio: Lima queda
# con unos 12 km y una ciudad de 200 mil con menos de 2 km.
SIGMA_KM_POR_MILLON = 3.8
# Una fraccion de los puntos cae en la periferia, con una dispersion cinco veces
# mayor: sin ella cada ciudad seria una sola nube compacta y no habria puntos
# aislados entre ciudades vecinas.
FRACCION_PERIFERIA = 0.10
FACTOR_PERIFERIA = 5.0


@dataclass(frozen=True)
class Ciudad:
    nombre: str
    latitud: float
    longitud: float
    poblacion: int

    @property
    def sigma_km(self) -> float:
        return SIGMA_KM_POR_MILLON * math.sqrt(self.poblacion / 1_000_000)


# Poblacion urbana aproximada del censo 2017 (INEI). Solo fija el peso relativo
# de cada ciudad; no se necesita mas precision que esa.
CIUDADES = (
    Ciudad("lima", -12.0464, -77.0428, 9_570_000),
    Ciudad("arequipa", -16.3989, -71.5350, 1_008_000),
    Ciudad("trujillo", -8.1116, -79.0288, 919_000),
    Ciudad("chiclayo", -6.7714, -79.8409, 552_000),
    Ciudad("piura", -5.1945, -80.6328, 484_000),
    Ciudad("cusco", -13.5320, -71.9675, 428_000),
    Ciudad("iquitos", -3.7491, -73.2538, 413_000),
    Ciudad("huancayo", -12.0651, -75.2049, 385_000),
    Ciudad("chimbote", -9.0745, -78.5936, 372_000),
    Ciudad("pucallpa", -8.3791, -74.5539, 311_000),
    Ciudad("tacna", -18.0066, -70.2463, 287_000),
    Ciudad("ica", -14.0678, -75.7286, 282_000),
    Ciudad("juliaca", -15.5000, -70.1333, 276_000),
    Ciudad("ayacucho", -13.1588, -74.2232, 216_000),
    Ciudad("cajamarca", -7.1617, -78.5128, 201_000),
)
_PESOS_ACUMULADOS = tuple(
    sum(ciudad.poblacion for ciudad in CIUDADES[: i + 1]) for i in range(len(CIUDADES))
)


def _punto_cerca(rng: random.Random, ciudad: Ciudad) -> tuple[float, float]:
    """Un punto alrededor de la ciudad con un desplazamiento normal en kilometros."""
    sigma = ciudad.sigma_km
    if rng.random() < FRACCION_PERIFERIA:
        sigma *= FACTOR_PERIFERIA
    norte_km = rng.gauss(0.0, sigma)
    este_km = rng.gauss(0.0, sigma)
    latitud = ciudad.latitud + norte_km / KM_POR_GRADO
    # Un grado de longitud se encoge con el coseno de la latitud: sin esta
    # correccion las nubes saldrian estiradas en el sentido este-oeste.
    longitud = ciudad.longitud + este_km / (KM_POR_GRADO * math.cos(math.radians(latitud)))
    return latitud, longitud


def generar_puntos(tamano: int) -> list[tuple[int, str, str, str]]:
    """Las filas del dataset, con las coordenadas ya como texto de seis decimales."""
    if tamano not in TAMANOS:
        raise ValueError(f"tamano no soportado: {tamano}; usa uno de {TAMANOS}")

    # Cada llamada empieza desde el mismo estado, aunque antes se haya generado otro tamano.
    rng = random.Random(SEMILLA)
    filas = []
    for identificador in range(1, tamano + 1):
        (ciudad,) = rng.choices(CIUDADES, cum_weights=_PESOS_ACUMULADOS)
        latitud, longitud = _punto_cerca(rng, ciudad)
        # Seis decimales son unos 11 cm: de sobra para un radio de 1 km, y fijan
        # el texto del CSV sin depender de como se imprime un double.
        filas.append((identificador, ciudad.nombre, f"{latitud:.6f}", f"{longitud:.6f}"))
    return filas


def generar_dataset(tamano: int, salida: Path) -> Path:
    """Escribe un CSV reproducible y devuelve su ruta; reemplaza ese archivo si existe."""
    filas = generar_puntos(tamano)
    salida.mkdir(parents=True, exist_ok=True)
    ruta = salida / f"puntos_{tamano}.csv"
    with ruta.open("w", encoding="utf-8", newline="") as archivo:
        escritor = csv.writer(archivo, lineterminator="\n")
        escritor.writerow(CABECERA)
        escritor.writerows(filas)
    return ruta


def main() -> None:
    """Lee las opciones de consola y genera todos los tamanos solicitados."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--salida",
        type=Path,
        default=SALIDA_PREDETERMINADA,
        help="carpeta de salida (por defecto: benchmarks/datasets junto al script)",
    )
    parser.add_argument(
        "--tamanos",
        nargs="+",
        type=int,
        choices=TAMANOS,
        default=TAMANOS,
        help="tamanos a generar (por defecto: 1000 10000 100000)",
    )
    args = parser.parse_args()
    for tamano in args.tamanos:
        ruta = generar_dataset(tamano, args.salida)
        print(f"{ruta}: {tamano} puntos")


if __name__ == "__main__":
    main()
