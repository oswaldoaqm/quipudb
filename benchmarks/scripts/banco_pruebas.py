"""Banco comun de mediciones aisladas; no depende de los bindings para importarse."""

from __future__ import annotations

import csv
import hashlib
import io
import os
import platform
import subprocess
import time
from collections import defaultdict
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from statistics import median
from tempfile import TemporaryDirectory, gettempdir
from uuid import uuid4

if __package__:
    from .generar_datasets import CABECERA, SEMILLA, TAMANOS
else:
    from generar_datasets import CABECERA, SEMILLA, TAMANOS

RAIZ = Path(__file__).resolve().parents[2]
RESULTADOS = RAIZ / "benchmarks" / "results"
CLAVES = ("ejecucion", "caso", "tecnica", "operacion", "n_registros", "n_operaciones")
METRICAS = (
    "tiempo_ns",
    "paginas_leidas",
    "paginas_escritas",
    "datos_bytes",
    "indices_bytes",
    "espacio_antes_bytes",
    "espacio_despues_bytes",
)
CABECERA_MEDICIONES = CLAVES + ("repeticion",) + METRICAS + ("estado",)
CABECERA_RESUMEN = (
    CLAVES
    + ("repeticiones",)
    + tuple(f"mediana_{campo}" for campo in METRICAS)
    + ("min_tiempo_ns", "max_tiempo_ns")
)
CABECERA_ENTORNO = ("ejecucion", "clave", "valor")


@dataclass(frozen=True)
class Dataset:
    ruta: Path
    sha256: str
    registros: tuple[tuple[int, str, float], ...]


@dataclass(frozen=True)
class Operacion:
    """Callbacks de un estado preparado. El contexto del caso se encarga de cerrarlo."""

    n_operaciones: int
    ejecutar: Callable[[], None]
    reiniciar_contadores: Callable[[], None]
    capturar_contadores: Callable[[], tuple[int, int]]  # lecturas, escrituras
    medir_espacio: Callable[[], tuple[int, int]]  # bytes de datos, bytes de indices
    validar: Callable[[], None]
    configuracion: dict[str, object]


@dataclass(frozen=True)
class Caso:
    nombre: str
    tecnica: str
    operacion: str
    preparar: Callable[[Path, Dataset], AbstractContextManager[Operacion]]


def cargar_dataset(ruta: Path, tamano: int) -> Dataset:
    """Lee, convierte y valida toda la entrada antes de medir cualquier operacion."""
    if tamano not in TAMANOS:
        raise ValueError(f"tamano no soportado: {tamano}; usa uno de {TAMANOS}")
    try:
        contenido = ruta.read_bytes()
    except FileNotFoundError as error:
        raise ValueError(
            f"dataset inexistente: {ruta}; ejecuta benchmarks/scripts/generar_datasets.py"
        ) from error

    registros = []
    codigos = set()
    try:
        lector = csv.reader(io.StringIO(contenido.decode("utf-8"), newline=""), strict=True)
        if next(lector, None) != list(CABECERA):
            raise ValueError("la cabecera debe ser codigo,nombre,promedio")
        for numero, fila in enumerate(lector, start=2):
            if len(fila) != 3:
                raise ValueError(f"fila {numero}: se requieren tres columnas")
            try:
                codigo, nombre, promedio = int(fila[0]), fila[1], float(fila[2])
            except ValueError as error:
                raise ValueError(f"fila {numero}: codigo o promedio no numerico") from error
            if not 1 <= codigo <= tamano or codigo in codigos:
                raise ValueError(f"fila {numero}: codigo duplicado o fuera de 1..{tamano}")
            if not nombre or len(nombre.encode("utf-8")) > 16 or "\0" in nombre:
                raise ValueError(f"fila {numero}: nombre incompatible con VARCHAR(16)")
            # Tambien rechaza NaN e infinitos.
            if not 0.0 <= promedio <= 20.0:
                raise ValueError(f"fila {numero}: promedio fuera de 0.00..20.00")
            codigos.add(codigo)
            registros.append((codigo, nombre, promedio))
        if len(registros) != tamano:
            raise ValueError(f"se esperaban {tamano} registros, se encontraron {len(registros)}")
    except (ValueError, UnicodeError, csv.Error) as error:
        raise ValueError(f"dataset invalido {ruta}: {error}") from error
    return Dataset(ruta.resolve(), hashlib.sha256(contenido).hexdigest(), tuple(registros))


def resumir(mediciones: Sequence[dict]) -> list[dict]:
    """Agrupa las muestras medidas sin descartar outliers; nunca recibe calentamientos."""
    grupos = defaultdict(list)
    for fila in mediciones:
        if fila["estado"] != "ok":
            raise ValueError("no se pueden resumir mediciones fallidas")
        grupos[tuple(fila[clave] for clave in CLAVES)].append(fila)
    resumen = []
    for clave, filas in grupos.items():
        resultado = dict(zip(CLAVES, clave))
        resultado["repeticiones"] = len(filas)
        for campo in METRICAS:
            resultado[f"mediana_{campo}"] = median(fila[campo] for fila in filas)
        resultado["min_tiempo_ns"] = min(fila["tiempo_ns"] for fila in filas)
        resultado["max_tiempo_ns"] = max(fila["tiempo_ns"] for fila in filas)
        resumen.append(resultado)
    return resumen


def registrar_entorno() -> dict[str, object]:
    """Registra datos observables; no adivina el hardware ni la compilacion del core."""
    reloj = time.get_clock_info("perf_counter")
    entorno = {
        "fecha_utc": datetime.now(UTC).isoformat(),
        "sistema": platform.platform(),
        "arquitectura": platform.machine(),
        "cpu": platform.processor() or "no_detectado",
        "cpu_logicas": os.cpu_count(),
        "python": platform.python_version(),
        "implementacion_python": platform.python_implementation(),
        "compilador_python": platform.python_compiler(),
        "ram": "no_documentado",
        "disco": "no_documentado",
        "compilador_core": "no_documentado",
        "compilacion_core": "no_documentado",
        "reloj": "time.perf_counter_ns",
        "reloj_implementacion": reloj.implementation,
        "reloj_resolucion_s": reloj.resolution,
        "cache_so": "no_controlada",
        "semilla_generador": SEMILLA,
    }
    for clave, argumentos in (
        ("git_commit", ["rev-parse", "HEAD"]),
        ("git_cambios", ["status", "--porcelain", "--untracked-files=normal"]),
    ):
        try:
            valor = subprocess.run(
                ["git", "-C", str(RAIZ), *argumentos],
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            ).stdout.strip()
            entorno[clave] = bool(valor) if clave == "git_cambios" else valor
        except (OSError, subprocess.SubprocessError):
            entorno[clave] = "no_disponible"
    return entorno


def escribir_csv(ruta: Path, cabecera: tuple[str, ...], filas: Sequence[dict]) -> None:
    """Creacion exclusiva: un resultado existente nunca se reemplaza."""
    with ruta.open("x", encoding="utf-8", newline="") as archivo:
        escritor = csv.DictWriter(archivo, fieldnames=cabecera, lineterminator="\n")
        escritor.writeheader()
        escritor.writerows(filas)


def ejecutar_banco(
    casos: Sequence[Caso],
    datasets: Sequence[Dataset],
    *,
    salida: Path = RESULTADOS,
    temporales: Path | None = None,
    calentamientos: int = 1,
    repeticiones: int = 5,
    notas: dict[str, str] | None = None,
) -> dict[str, Path]:
    """Ejecuta casos aislados y exporta solo cuando todos terminaron correctamente."""
    if calentamientos < 0 or repeticiones < 1:
        raise ValueError("calentamientos debe ser >= 0 y repeticiones debe ser >= 1")
    if not casos or not datasets:
        raise ValueError("se requiere al menos un caso y un dataset")
    if len({caso.nombre for caso in casos}) != len(casos):
        raise ValueError("los nombres de los casos deben ser unicos")
    if len({len(dataset.registros) for dataset in datasets}) != len(datasets):
        raise ValueError("no se puede repetir el mismo tamano de dataset")
    base_temporal = Path(temporales if temporales is not None else gettempdir()).resolve()
    if not base_temporal.is_dir():
        raise ValueError(f"la carpeta base de temporales no existe: {base_temporal}")

    identificador = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex
    entorno = registrar_entorno()
    entorno.update(
        {
            "calentamientos": calentamientos,
            "repeticiones": repeticiones,
            "temporales": str(base_temporal),
            "salida": str(salida.resolve()),
        }
    )
    # Las declaraciones manuales no pueden sobrescribir datos detectados automaticamente.
    entorno.update({f"declarado.{clave}": valor for clave, valor in (notas or {}).items()})
    mediciones = []
    for dataset in datasets:
        tamano = len(dataset.registros)
        entorno[f"dataset.{tamano}.ruta"] = str(dataset.ruta)
        entorno[f"dataset.{tamano}.sha256"] = dataset.sha256
        for caso in casos:
            configuracion = None
            for repeticion in range(-calentamientos + 1, repeticiones + 1):
                with (
                    TemporaryDirectory(prefix="quipudb_bench_", dir=base_temporal) as temporal,
                    caso.preparar(Path(temporal), dataset) as operacion,
                ):
                    if operacion.n_operaciones < 1:
                        raise ValueError("n_operaciones debe ser >= 1")
                    actual = (dict(operacion.configuracion), operacion.n_operaciones)
                    if configuracion is not None and configuracion != actual:
                        raise ValueError("la configuracion cambio entre repeticiones")
                    configuracion = actual
                    espacio_antes = sum(operacion.medir_espacio())
                    operacion.reiniciar_contadores()
                    inicio = time.perf_counter_ns()
                    operacion.ejecutar()
                    tiempo_ns = time.perf_counter_ns() - inicio
                    # Ninguna validacion ni consulta de archivos entre el reloj y esta copia.
                    paginas_leidas, paginas_escritas = operacion.capturar_contadores()
                    datos_bytes, indices_bytes = operacion.medir_espacio()
                    operacion.validar()
                    if repeticion > 0:
                        mediciones.append(
                            {
                                "ejecucion": identificador,
                                "caso": caso.nombre,
                                "tecnica": caso.tecnica,
                                "operacion": caso.operacion,
                                "n_registros": tamano,
                                "n_operaciones": operacion.n_operaciones,
                                "repeticion": repeticion,
                                "tiempo_ns": tiempo_ns,
                                "paginas_leidas": paginas_leidas,
                                "paginas_escritas": paginas_escritas,
                                "datos_bytes": datos_bytes,
                                "indices_bytes": indices_bytes,
                                "espacio_antes_bytes": espacio_antes,
                                "espacio_despues_bytes": datos_bytes + indices_bytes,
                                "estado": "ok",
                            }
                        )
            for clave, valor in configuracion[0].items():
                entorno[f"caso.{caso.nombre}.{tamano}.{clave}"] = valor

    salida.mkdir(parents=True, exist_ok=True)
    rutas = {
        tipo: salida / f"{identificador}_{tipo}.csv"
        for tipo in ("mediciones", "resumen", "entorno")
    }
    escribir_csv(rutas["mediciones"], CABECERA_MEDICIONES, mediciones)
    escribir_csv(rutas["resumen"], CABECERA_RESUMEN, resumir(mediciones))
    escribir_csv(
        rutas["entorno"],
        CABECERA_ENTORNO,
        [
            {"ejecucion": identificador, "clave": clave, "valor": valor}
            for clave, valor in sorted(entorno.items())
        ],
    )
    return rutas
