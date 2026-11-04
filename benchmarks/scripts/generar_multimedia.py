"""Genera PNG/WAV sinteticos y precalcula sus vectores en etapas independientes."""

from __future__ import annotations

import argparse
import csv
import sys
import tempfile
import time
import wave
from pathlib import Path

import numpy as np

# Permite ejecutar el script desde fuera del repositorio sin instalar el motor.
if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from benchmarks.scripts.multimedia_dataset import (  # noqa: E402
    CABECERA,
    FORMATO,
    MODALIDADES,
    SALIDA,
    SEMILLA,
    escribir_json,
    filas,
    leer_generacion,
    sha256,
    validar_vectores,
    versiones,
)

LADO = 128
FRECUENCIA = 16_000
MUESTRAS = 8_000  # medio segundo; 48 ventanas MFCC con los parametros del motor


def imagen(rng: np.random.Generator) -> np.ndarray:
    """Manchas, anillos y segmentos de distintas escalas y contrastes."""
    import cv2

    fondo = int(rng.integers(155, 241))
    datos = np.full((LADO, LADO), fondo, dtype=np.uint8)
    for _ in range(int(rng.integers(6, 19))):
        centro = tuple(int(x) for x in rng.integers(16, LADO - 16, 2))
        radio = int(rng.integers(3, 17))
        color = int(rng.integers(5, 140))
        cv2.circle(datos, centro, radio, color, -1 if rng.random() < 0.7 else 2)
    for _ in range(int(rng.integers(1, 6))):
        extremos = rng.integers(4, LADO - 4, (2, 2)).tolist()
        cv2.line(datos, tuple(extremos[0]), tuple(extremos[1]), int(rng.integers(20, 150)), 2)
    ruido = rng.integers(-3, 4, datos.shape, dtype=np.int16)
    return np.clip(datos.astype(np.int16) + ruido, 0, 255).astype(np.uint8)


def audio(rng: np.random.Generator) -> np.ndarray:
    """Tonos armonicos con barrido, modulacion y una envolvente suave; PCM16 mono."""
    t = np.arange(MUESTRAS, dtype=np.float64) / FRECUENCIA
    fundamental = rng.uniform(120, 1600)
    barrido = rng.uniform(-0.4, 0.8) * fundamental
    fase = 2 * np.pi * (fundamental * t + barrido * t * t)
    senal = np.zeros(MUESTRAS)
    for armonico in range(1, int(rng.integers(2, 6))):
        senal += rng.uniform(0.2, 1) / armonico * np.sin(
            armonico * fase + rng.uniform(0, 2 * np.pi)
        )
    modulacion = 0.65 + 0.35 * np.sin(2 * np.pi * rng.uniform(2, 12) * t)
    senal *= modulacion * np.sin(np.pi * np.arange(MUESTRAS) / (MUESTRAS - 1)) ** 2
    senal += rng.normal(0, 0.002, MUESTRAS)
    senal *= 0.8 / np.max(np.abs(senal))
    return np.round(senal * 32767).astype("<i2")


def generar(salida: Path, modalidad: str, cantidad: int, semilla: int = SEMILLA) -> Path:
    if modalidad not in MODALIDADES or not 1 <= cantidad <= 100_000 or semilla < 0:
        raise ValueError("modalidad, cantidad [1, 100000] o semilla no valida")
    destino = salida / f"{modalidad}_{cantidad}"
    if destino.exists():
        raise ValueError(f"{destino} ya existe; use otra carpeta de salida")
    entorno = versiones()
    if modalidad == "imagenes":
        import cv2

        entorno["opencv"] = cv2.__version__
    salida.mkdir(parents=True, exist_ok=True)
    # Solo se publica un dataset completo. Los temporales propios se limpian ante un error.
    with tempfile.TemporaryDirectory(prefix=".generacion-", dir=salida) as temporal:
        raiz = Path(temporal) / "dataset"
        (raiz / "archivos").mkdir(parents=True)
        with (raiz / "objetos.csv").open("w", encoding="utf-8", newline="") as archivo:
            escritor = csv.writer(archivo, lineterminator="\n")
            escritor.writerow(CABECERA)
            for identificador in range(1, cantidad + 1):
                rng = np.random.default_rng(
                    np.random.SeedSequence([semilla, MODALIDADES.index(modalidad), identificador])
                )
                extension = "png" if modalidad == "imagenes" else "wav"
                nombre = f"{identificador:06d}.{extension}"
                ruta = raiz / "archivos" / nombre
                if modalidad == "imagenes":
                    ok, png = cv2.imencode(".png", imagen(rng), [cv2.IMWRITE_PNG_COMPRESSION, 6])
                    if not ok:
                        raise ValueError(f"no se pudo codificar {nombre}")
                    ruta.write_bytes(png.tobytes())
                else:
                    with wave.open(str(ruta), "wb") as wav:
                        wav.setnchannels(1)
                        wav.setsampwidth(2)
                        wav.setframerate(FRECUENCIA)
                        wav.writeframes(audio(rng).tobytes())
                escritor.writerow((identificador, nombre, f"archivos/{nombre}", sha256(ruta)))
        escribir_json(raiz / "generacion.json", {
            "formato": FORMATO, "origen": "sintetico; generar_multimedia.py, algoritmo v1",
            "modalidad": modalidad, "cantidad": cantidad, "semilla": semilla,
            "parametros": {"lado": LADO} if modalidad == "imagenes" else {
                "frecuencia": FRECUENCIA, "muestras": MUESTRAS, "canales": 1, "bits": 16,
            },
            "versiones": entorno, "manifiesto_sha256": sha256(raiz / "objetos.csv"),
        })
        raiz.rename(destino)
    return destino


def precalcular(raiz: Path, *, k: int = 64, muestra: int = 8192) -> Path:
    """Muestra acotada para K-Means; DF sobre TODOS los objetos, sin guardar descriptores."""
    from engine.multimedia.features import Vocabulary, describe
    from engine.multimedia.kmeans import MAX_ITERACIONES, TOLERANCIA, build_codebook
    from engine.multimedia.weights import TF_LOG, inverse_document_frequency, tf_weight

    if not 2 <= k <= 256 or muestra < k:
        raise ValueError("k debe estar entre 2 y 256 y muestra debe ser >= k")
    datos = leer_generacion(raiz)
    destino = raiz / "caracteristicas"
    if destino.exists():
        raise ValueError(f"{destino} ya existe; no se reemplaza un precalculo")
    # Verificar todos los archivos antes de entrenar.
    for _ in filas(raiz, datos):
        pass
    cantidad = datos["cantidad"]
    semilla = datos["semilla"]
    opciones = {"max_keypoints": 500} if datos["modalidad"] == "imagenes" else {
        "n_mfcc": 13, "frame_ms": 25.0, "hop_ms": 10.0, "n_mels": 26, "max_seconds": 30.0,
    }
    entorno = versiones()
    if datos["modalidad"] == "imagenes":
        import cv2

        entorno["opencv"] = cv2.__version__
    else:
        import soundfile

        entorno.update(soundfile=soundfile.__version__, libsndfile=soundfile.__libsndfile_version__)

    rng = np.random.default_rng(np.random.SeedSequence([semilla, 190]))
    elegidos = np.sort(rng.choice(cantidad, min(128, cantidad, muestra), replace=False)) + 1
    por_objeto = muestra // len(elegidos)
    muestra_local = []
    for identificador, _, ruta in filas(raiz, datos, verificar_archivos=False):
        if identificador in elegidos:
            descriptores = describe(ruta, **opciones)
            indices = np.sort(rng.choice(len(descriptores), min(por_objeto, len(descriptores)),
                                         replace=False))
            muestra_local.append(descriptores[indices])
    descriptores_muestra = np.concatenate(muestra_local)
    cantidad_muestra = len(descriptores_muestra)
    codebook = build_codebook(descriptores_muestra, k, seed=semilla)
    del descriptores_muestra
    del muestra_local

    with tempfile.TemporaryDirectory(prefix=".precalculo-", dir=raiz) as temporal:
        trabajo = Path(temporal)
        resultado = trabajo / "caracteristicas"
        resultado.mkdir()
        # N x K conteos en disco: permite calcular IDF sin repetir la extraccion completa.
        conteos = np.lib.format.open_memmap(
            trabajo / "conteos.npy", mode="w+", dtype="uint32", shape=(cantidad, k)
        )
        df = np.zeros(k, dtype=np.int64)
        for identificador, _, ruta in filas(raiz, datos, verificar_archivos=False):
            palabras = codebook.assign(describe(ruta, **opciones))
            conteos[identificador - 1] = np.bincount(palabras, minlength=k)
            df += conteos[identificador - 1] > 0
        vocabulario = Vocabulary(codebook, inverse_document_frequency(df, cantidad), cantidad)
        vocabulario.save(resultado / "vocabulario.npz")
        vectores = np.lib.format.open_memmap(
            resultado / "vectores.npy", mode="w+", dtype="float32", shape=(cantidad, k)
        )
        for inicio in range(0, cantidad, 1024):
            # Mismos pesos y normalizacion de Vocabulary.histogram, sobre conteos cacheados.
            pesos = tf_weight(conteos[inicio : inicio + 1024], TF_LOG) * vocabulario.idf
            normas = np.sqrt(np.sum(pesos * pesos, axis=1))
            if np.any(normas == 0):
                raise ValueError("el vocabulario produce vectores nulos; ajuste k o los patrones")
            vectores[inicio : inicio + len(pesos)] = (pesos / normas[:, None]).astype(np.float32)
        validar_vectores(vectores, cantidad, k)
        vectores.flush()
        del vectores, conteos
        escribir_json(resultado / "metadata.json", {
            "formato": FORMATO, "generacion_sha256": sha256(raiz / "generacion.json"),
            "k": k, "semilla": semilla, "tf": TF_LOG, "normalizacion": "l2",
            "extraccion": opciones, "muestra_maxima": muestra,
            "descriptores_entrenamiento": cantidad_muestra,
            "kmeans": {"max_iterations": MAX_ITERACIONES, "tolerance": TOLERANCIA},
            "objetos_entrenamiento": elegidos.tolist(), "versiones": entorno,
            "vectores_nulos": 0,
            "sha256": {nombre: sha256(resultado / nombre)
                       for nombre in ("vectores.npy", "vocabulario.npz")},
        })
        resultado.rename(destino)
    return destino


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    comandos = parser.add_subparsers(dest="etapa", required=True)
    generacion = comandos.add_parser("generar", help="solo escribir PNG/WAV y manifiesto")
    generacion.add_argument("--modalidad", choices=(*MODALIDADES, "ambas"), default="ambas")
    generacion.add_argument("--tamanos", nargs="+", type=int, default=[1000],
                            help="1 a 100000; oficiales: 1000 10000 100000")
    generacion.add_argument("--semilla", type=int, default=SEMILLA)
    generacion.add_argument("--salida", type=Path, default=SALIDA)
    precalculo = comandos.add_parser("precalcular", help="entrenar y guardar los vectores")
    precalculo.add_argument("datasets", type=Path, nargs="+")
    precalculo.add_argument("--k", type=int, default=64)
    precalculo.add_argument("--muestra", type=int, default=8192)
    args = parser.parse_args()
    try:
        inicio = time.perf_counter()
        if args.etapa == "generar":
            modalidades = MODALIDADES if args.modalidad == "ambas" else (args.modalidad,)
            for modalidad in modalidades:
                for cantidad in args.tamanos:
                    print(generar(args.salida, modalidad, cantidad, args.semilla), flush=True)
        else:
            for raiz in args.datasets:
                print(precalcular(raiz.resolve(), k=args.k, muestra=args.muestra), flush=True)
        print(f"{args.etapa}: {time.perf_counter() - inicio:.3f} s")
    except (ValueError, OSError, ImportError) as error:
        parser.exit(1, f"error: {error}\n")


if __name__ == "__main__":
    main()
