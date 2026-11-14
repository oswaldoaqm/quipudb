"""Carga en el motor real las tablas de demostracion de las Partes 3 y 4.

Todo pasa por la API, por los mismos endpoints que usa el frontend:
``POST /query`` para el SQL, ``POST /tables/{t}/load`` para el CSV y
``POST /tables/{t}/load-media`` para las carpetas multimedia. Lo unico que se
hace fuera de la API es lo que la API no hace a proposito: dibujar las
imagenes, sintetizar los audios y entrenar sus vocabularios (#187: "No se
entrena un vocabulario al llamar al endpoint").

Tablas que deja:

- ``articulos``: 20 resumenes en espanol, indice FULLTEXT (spanish).
- ``documentos``: el corpus de Quora de ``benchmarks/datasets/documentos_1000.csv``
  si existe, indice FULLTEXT (english).
- ``imagenes``: 48 PNG en 6 familias visuales, ``VECTOR(32)``, HNSW (cosine) e IVF.
- ``canciones``: 36 WAV en 6 familias sonoras, ``VECTOR(24)``, IVF y HNSW (euclidean).

Ademas deja ``foto_consulta.jpg`` y ``audio_query.wav`` en la raiz multimedia
para las consultas del enunciado. Se corre dentro del contenedor:

    docker compose exec motor python scripts/sembrar_demo.py [--rehacer]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import numpy as np

API = os.environ.get("QUIPUDB_API", "http://localhost:8000")
RAIZ = Path(os.environ.get("QUIPUDB_MEDIA_ROOT", "datos/multimedia"))
CORPUS = Path("benchmarks/datasets/documentos_1000.csv")

K_IMAGENES = 32
K_AUDIO = 24
POR_FAMILIA_IMAGEN = 8
POR_FAMILIA_AUDIO = 6
LADO = 192
FRECUENCIA = 16_000


# --- Cliente HTTP minimo (solo biblioteca estandar) -------------------------

def _pedir(ruta: str, cuerpo: bytes | None = None, tipo: str | None = None) -> dict:
    peticion = urllib.request.Request(f"{API}{ruta}", data=cuerpo, method="POST" if cuerpo is not None else "GET")
    if tipo:
        peticion.add_header("Content-Type", tipo)
    try:
        with urllib.request.urlopen(peticion, timeout=600) as respuesta:
            return json.loads(respuesta.read())
    except urllib.error.HTTPError as error:
        detalle = error.read().decode("utf-8", "replace")
        raise SystemExit(f"{ruta} respondio {error.code}: {detalle}") from None


def sql(sentencia: str) -> dict:
    return _pedir("/query", json.dumps({"sql": sentencia}).encode(), "application/json")


def tablas() -> set[str]:
    return {t["name"] for t in _pedir("/tables")}


def cargar_csv(tabla: str, archivo: Path) -> dict:
    limite = uuid.uuid4().hex
    cuerpo = (
        f"--{limite}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{archivo.name}\"\r\n"
        "Content-Type: text/csv\r\n\r\n"
    ).encode() + archivo.read_bytes() + f"\r\n--{limite}--\r\n".encode()
    return _pedir(f"/tables/{tabla}/load", cuerpo, f"multipart/form-data; boundary={limite}")


def cargar_multimedia(tabla: str, carpeta: str, vocabulario: str) -> dict:
    trabajo = _pedir(
        f"/tables/{tabla}/load-media",
        json.dumps({"folder": carpeta, "vocabulary": vocabulario}).encode(),
        "application/json",
    )
    while trabajo["status"] in {"pending", "running"}:
        time.sleep(0.5)
        trabajo = _pedir(f"/tables/{tabla}/load-media/{trabajo['job_id']}")
    if trabajo["status"] != "completed":
        raise SystemExit(f"la carga de {tabla} termino en {trabajo['status']}: {trabajo.get('error')}")
    return trabajo


def preparar(tabla: str, rehacer: bool) -> bool:
    """True si hay que crear la tabla; con --rehacer la borra antes."""
    if tabla in tablas():
        if not rehacer:
            print(f"  {tabla}: ya existe, se deja como esta (usa --rehacer para recrearla)")
            return False
        sql(f"DROP TABLE {tabla}")
    return True


# --- Parte 3: texto ---------------------------------------------------------

ARTICULOS = [
    ("Introduccion al machine learning", "r. salas", 2019,
     "El machine learning aprende patrones a partir de datos etiquetados para predecir casos nuevos"),
    ("Deep learning para vision", "m. quispe", 2021,
     "Las redes neuronales convolucionales aprenden a reconocer objetos en imagenes sin descriptores manuales"),
    ("Machine learning en bases de datos", "a. torres", 2022,
     "Integrar machine learning dentro del motor evita mover los datos y acelera el entrenamiento"),
    ("Optimizacion de consultas aprendida", "l. vega", 2020,
     "Un optimizador basado en learning estima cardinalidades mejor que los histogramas clasicos"),
    ("Indices aprendidos", "c. ramos", 2018,
     "Un modelo de machine learning puede reemplazar a un arbol B+ prediciendo la posicion de cada clave"),
    ("Recuperacion de informacion clasica", "j. paz", 2015,
     "El modelo vectorial con TF-IDF y similitud del coseno sigue siendo la base de los buscadores"),
    ("BM25 en la practica", "e. mora", 2017,
     "BM25 supera a TF-IDF en colecciones con documentos de longitudes muy distintas"),
    ("Busqueda semantica con embeddings", "d. lima", 2023,
     "Los embeddings capturan el significado y complementan la busqueda por palabras clave"),
    ("Busqueda hibrida", "g. cano", 2024,
     "Fusionar BM25 con similitud vectorial mediante reciprocal rank fusion mejora la recuperacion"),
    ("Grafos de vecinos para busqueda aproximada", "h. soto", 2020,
     "HNSW construye capas de grafos y logra alto recall con pocas comparaciones de distancia"),
    ("Cuantizacion de vectores", "i. bravo", 2019,
     "IVF y la cuantizacion por productos reducen la memoria de un indice vectorial de alta dimension"),
    ("Bag of visual words", "k. rojas", 2012,
     "SIFT extrae descriptores locales y k-means los agrupa en palabras visuales para comparar imagenes"),
    ("Coeficientes MFCC para audio", "n. flores", 2014,
     "Los MFCC resumen el espectro de cada ventana de audio en la escala mel"),
    ("Aprendizaje por refuerzo", "o. chavez", 2021,
     "Un agente aprende una politica por ensayo y error maximizando una recompensa acumulada"),
    ("Machine learning responsable", "p. guzman", 2023,
     "Los modelos de machine learning deben auditarse para detectar sesgos en los datos de entrenamiento"),
    ("Transacciones distribuidas", "s. ibarra", 2016,
     "El protocolo de dos fases coordina la confirmacion de una transaccion entre varios nodos"),
    ("Bases de datos espaciales", "t. ponce", 2013,
     "El R-Tree y sus variantes indexan rectangulos y responden consultas de rango y vecinos cercanos"),
    ("Procesamiento de lenguaje natural", "v. castro", 2022,
     "Los modelos de lenguaje aprenden representaciones de texto con learning auto supervisado"),
    ("Sistemas de recomendacion", "w. medina", 2018,
     "El filtrado colaborativo recomienda productos a partir de las preferencias de usuarios similares"),
    ("Evaluacion de buscadores", "x. silva", 2016,
     "Precision@10 y recall@10 comparan rankings contra juicios de relevancia humanos"),
]


def sembrar_texto(rehacer: bool) -> None:
    print("Parte 3: texto")
    if preparar("articulos", rehacer):
        sql(
            "CREATE TABLE articulos (id INT PRIMARY KEY, titulo VARCHAR(64), autor VARCHAR(24), "
            "anio INT, texto VARCHAR(256)) USING HEAP"
        )
        inserts = ";".join(
            f"INSERT INTO articulos VALUES ({i}, '{t}', '{a}', {anio}, '{x}')"
            for i, (t, a, anio, x) in enumerate(ARTICULOS, 1)
        )
        print(f"  articulos: {sql(inserts)['affected_rows']} filas")
        sql("CREATE INDEX articulos_texto ON articulos (texto) USING FULLTEXT WITH LANGUAGE=spanish")

    if not CORPUS.exists():
        print(f"  documentos: falta {CORPUS}; generalo con benchmarks/scripts/generar_documentos.py")
        return
    if preparar("documentos", rehacer):
        sql("CREATE TABLE documentos (id INT PRIMARY KEY, quora_id INT, texto VARCHAR(256)) USING HEAP")
        reporte = cargar_csv("documentos", CORPUS)
        print(f"  documentos: {reporte['inserted']} filas, {reporte['failed']} con error")
        sql("CREATE INDEX documentos_texto ON documentos (texto) USING FULLTEXT WITH LANGUAGE=english")


# --- Parte 4: imagenes ------------------------------------------------------

def _imagen(familia: str, rng: np.random.Generator) -> np.ndarray:
    """Seis familias con texturas que SIFT distingue: bordes, esquinas, blobs."""
    import cv2

    fondo = int(rng.integers(170, 236))
    img = np.full((LADO, LADO), fondo, np.uint8)
    tinta = lambda: int(rng.integers(10, 110))
    if familia == "circulos":
        for _ in range(int(rng.integers(5, 10))):
            centro = tuple(int(x) for x in rng.integers(25, LADO - 25, 2))
            cv2.circle(img, centro, int(rng.integers(8, 28)), tinta(), int(rng.choice([-1, 3])))
    elif familia == "rejilla":
        # Lineas cruzadas: SIFT descarta los bordes rectos, pero no los cruces.
        paso = int(rng.integers(18, 30))
        desfase = int(rng.integers(0, paso))
        for d in range(desfase, LADO, paso):
            cv2.line(img, (d, 0), (d, LADO - 1), tinta(), 2)
            cv2.line(img, (0, d), (LADO - 1, d), tinta(), 2)
    elif familia == "tablero":
        lado = int(rng.integers(16, 30))
        for y in range(0, LADO, lado):
            for x in range(0, LADO, lado):
                if (x // lado + y // lado) % 2 == 0:
                    cv2.rectangle(img, (x, y), (x + lado - 1, y + lado - 1), tinta(), -1)
    elif familia == "letras":
        for _ in range(int(rng.integers(6, 11))):
            letra = chr(int(rng.integers(65, 91)))
            origen = (int(rng.integers(5, LADO - 40)), int(rng.integers(35, LADO - 5)))
            cv2.putText(img, letra, origen, cv2.FONT_HERSHEY_SIMPLEX, float(rng.uniform(1.0, 1.8)), tinta(), 3)
    elif familia == "triangulos":
        for _ in range(int(rng.integers(4, 8))):
            vertices = rng.integers(10, LADO - 10, (3, 2)).astype(np.int32)
            cv2.fillPoly(img, [vertices], tinta())
    elif familia == "estrellas":
        for _ in range(int(rng.integers(5, 9))):
            cx, cy = (int(x) for x in rng.integers(25, LADO - 25, 2))
            r = int(rng.integers(10, 24))
            for k in range(6):
                a = k * np.pi / 6 + float(rng.uniform(0, 0.3))
                cv2.line(img, (int(cx - r * np.cos(a)), int(cy - r * np.sin(a))),
                         (int(cx + r * np.cos(a)), int(cy + r * np.sin(a))), tinta(), 2)
    ruido = rng.integers(-4, 5, img.shape, dtype=np.int16)
    return np.clip(img.astype(np.int16) + ruido, 0, 255).astype(np.uint8)


FAMILIAS_IMAGEN = ("circulos", "rejilla", "tablero", "letras", "triangulos", "estrellas")


# --- Parte 4: audio ---------------------------------------------------------

def _audio(familia: str, rng: np.random.Generator) -> np.ndarray:
    """Dos segundos mono a 16 kHz de una familia.

    Un tono puro da la misma ventana MFCC de principio a fin, y todas las
    ventanas caen en la misma palabra auditiva: dos audios de la familia
    quedarian a distancia 0. El vibrato y la envolvente de `_tramo` hacen del
    histograma una distribucion. Se probo ademas mezclar un tramo de otra
    familia: bajaba el acierto 1-NN por familia de 94 % a 78 % (K=24).
    """
    senal = _tramo(familia, 2 * FRECUENCIA, rng)
    senal = senal + rng.normal(0, rng.uniform(0.002, 0.01), senal.size)
    return (0.8 * senal / np.max(np.abs(senal))).astype(np.float32)


def _tramo(familia: str, muestras: int, rng: np.random.Generator) -> np.ndarray:
    """``muestras`` de una familia, con vibrato y una envolvente lenta."""
    t = np.arange(muestras) / FRECUENCIA
    vibrato = 1 + rng.uniform(0.002, 0.02) * np.sin(2 * np.pi * rng.uniform(3, 7) * t)
    t = np.cumsum(vibrato) / FRECUENCIA
    if familia == "graves":
        f = rng.uniform(70, 160)
        senal = sum(np.sin(2 * np.pi * f * h * t) / h for h in range(1, 5))
    elif familia == "agudos":
        senal = np.sin(2 * np.pi * rng.uniform(2500, 4000) * t)
    elif familia == "acordes":
        base = rng.uniform(500, 900)
        senal = sum(np.sin(2 * np.pi * base * r * t) for r in (1, 1.25, 1.5))
    elif familia == "ruido":
        senal = np.convolve(rng.normal(0, 1, t.size), np.ones(4) / 4, mode="same")
    elif familia == "barrido":
        f0, f1 = rng.uniform(200, 600), rng.uniform(2000, 5000)
        senal = np.sin(2 * np.pi * (f0 * t + (f1 - f0) * t**2 / 2))
    else:  # pulsos
        senal = np.zeros(t.size)
        periodo = int(FRECUENCIA / rng.uniform(4, 9))
        for inicio in range(0, t.size, periodo):
            fin = min(t.size, inicio + 400)
            senal[inicio:fin] = np.sin(2 * np.pi * 1000 * t[: fin - inicio]) * np.exp(-np.arange(fin - inicio) / 80)
    envolvente = 0.6 + 0.4 * np.sin(2 * np.pi * rng.uniform(0.5, 2) * t + rng.uniform(0, np.pi))
    senal = senal * envolvente
    return senal / max(1e-9, float(np.max(np.abs(senal))))


FAMILIAS_AUDIO = ("graves", "agudos", "acordes", "ruido", "barrido", "pulsos")


def _entrenar(archivos: list[Path], k: int, destino: Path, **opciones: object) -> None:
    from engine.multimedia.features import build_vocabulary, describe

    descriptores = [describe(ruta, **opciones) for ruta in archivos]
    build_vocabulary(descriptores, k=k, seed=187).save(destino)


def sembrar_multimedia(rehacer: bool) -> None:
    import cv2
    import soundfile as sf

    print("Parte 4: multimedia")
    rng = np.random.default_rng(190)

    if preparar("imagenes", rehacer):
        carpeta = RAIZ / "imagenes"
        carpeta.mkdir(parents=True, exist_ok=True)
        archivos = []
        for familia in FAMILIAS_IMAGEN:
            for i in range(1, POR_FAMILIA_IMAGEN + 1):
                ruta = carpeta / f"{familia}_{i:02d}.png"
                cv2.imwrite(str(ruta), _imagen(familia, rng))
                archivos.append(ruta)
        # Fuera de la carpeta cargada: es una foto "nueva", no una de la tabla.
        cv2.imwrite(str(RAIZ / "foto_consulta.jpg"), _imagen("circulos", rng))
        _entrenar(archivos, K_IMAGENES, RAIZ / "imagenes.npz", max_keypoints=500)
        sql(
            "CREATE TABLE imagenes (id INT PRIMARY KEY, nombre VARCHAR(128), ruta VARCHAR(1024), "
            f"rasgos VECTOR({K_IMAGENES})) USING HEAP"
        )
        trabajo = cargar_multimedia("imagenes", "imagenes", "imagenes.npz")
        print(f"  imagenes: {trabajo['inserted']} filas, {trabajo['failed']} con error")
        sql("CREATE INDEX imagenes_hnsw ON imagenes (rasgos) USING HNSW WITH METRIC=cosine")
        sql("CREATE INDEX imagenes_ivf ON imagenes (rasgos) USING IVF")

    if preparar("canciones", rehacer):
        carpeta = RAIZ / "canciones"
        carpeta.mkdir(parents=True, exist_ok=True)
        archivos = []
        for familia in FAMILIAS_AUDIO:
            for i in range(1, POR_FAMILIA_AUDIO + 1):
                ruta = carpeta / f"{familia}_{i:02d}.wav"
                sf.write(ruta, _audio(familia, rng), FRECUENCIA)
                archivos.append(ruta)
        sf.write(RAIZ / "audio_query.wav", _audio("acordes", rng), FRECUENCIA)
        _entrenar(archivos, K_AUDIO, RAIZ / "canciones.npz", max_seconds=30.0)
        sql(
            "CREATE TABLE canciones (id INT PRIMARY KEY, nombre VARCHAR(128), ruta VARCHAR(1024), "
            f"rasgos VECTOR({K_AUDIO})) USING HEAP"
        )
        trabajo = cargar_multimedia("canciones", "canciones", "canciones.npz")
        print(f"  canciones: {trabajo['inserted']} filas, {trabajo['failed']} con error")
        sql("CREATE INDEX canciones_ivf ON canciones (rasgos) USING IVF")
        sql("CREATE INDEX canciones_hnsw ON canciones (rasgos) USING HNSW WITH METRIC=euclidean")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--rehacer", action="store_true", help="borra y vuelve a crear las tablas de demo")
    argumentos = parser.parse_args()
    try:
        tablas()
    except urllib.error.URLError as error:
        sys.exit(f"no se pudo contactar a la API en {API}: {error.reason}")
    sembrar_texto(argumentos.rehacer)
    sembrar_multimedia(argumentos.rehacer)
    print("Listo. Tablas:", ", ".join(sorted(tablas())))


if __name__ == "__main__":
    main()
