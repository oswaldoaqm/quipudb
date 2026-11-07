"""Embeddings de los chunks y su carga en QuipuDB (#252).

Dos pasos, los dos se pueden cortar y volver a correr sin repetir trabajo:

1. ``embeber``: pide a Gemini el embedding de cada chunk que aun no lo tiene y
   lo agrega a una cache binaria en ``quipucamayoc/datos/embeddings/``. La cuota
   gratuita de embeddings es de unos 30 000 tokens por minuto y un chunk tiene
   ~670: los 6 226 chunks del corpus tardan unas 2,5 horas. Con la cache no se
   vuelven a pagar.
2. ``cargar``: inserta en ``chunk_vectores`` los vectores que faltan y crea el
   indice HNSW con coseno si no existe. Tambien crea el hash sobre ``chunks.id``,
   que la busqueda vectorial usa para traer el texto de cada chunk (#255).

Uso, desde ``quipucamayoc/server`` y con el motor levantado::

    python -m app.corpus.vectores embeber
    python -m app.corpus.vectores cargar
"""

from __future__ import annotations

import argparse
import struct
import sys
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from app.config import load_settings
from app.corpus.arxiv import DATOS
from app.corpus.carga import CORPUS
from app.corpus.esquema import Tablas
from app.motor import ClienteMotor, literal_vector
from app.rag.embeddings import DIMENSION, Embedder, GeminiEmbedder, LimiteDeUso

LOTE = 10
TOKENS_POR_MINUTO = 25_000
"""Un poco por debajo de la cuota medida (~30 000): deja margen para preguntar
mientras se embebe."""
TOKENS_POR_PALABRA = 2.3
"""Medido: 10 chunks de 300 palabras son 6 693 tokens."""
INSERTS_POR_PETICION = 25

_REGISTRO = struct.Struct(f"<i{DIMENSION}f")


# ---------------------------------------------------------------------------
# Cache binaria: (chunk_id int32, DIMENSION x float32) por registro
# ---------------------------------------------------------------------------


def ruta_cache(modelo: str, carpeta: Path = DATOS / "embeddings") -> Path:
    return carpeta / f"{modelo}_{DIMENSION}.bin"


def leer_cache(ruta: Path) -> dict[int, tuple[float, ...]]:
    """Los vectores guardados. Un registro cortado al final (un corte a mitad
    de una escritura) se ignora: se vuelve a pedir."""

    if not ruta.is_file():
        return {}
    datos = ruta.read_bytes()
    completos = len(datos) - len(datos) % _REGISTRO.size
    vectores = {}
    for desde in range(0, completos, _REGISTRO.size):
        chunk_id, *componentes = _REGISTRO.unpack_from(datos, desde)
        vectores[chunk_id] = tuple(componentes)
    return vectores


def agregar_a_cache(ruta: Path, vectores: Sequence[tuple[int, Sequence[float]]]) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    tamanio = ruta.stat().st_size if ruta.is_file() else 0
    with ruta.open("r+b" if tamanio else "wb") as archivo:
        # Si la corrida anterior se corto a mitad de un registro, se descarta
        # ese resto antes de seguir escribiendo.
        archivo.truncate(tamanio - tamanio % _REGISTRO.size)
        archivo.seek(0, 2)
        for chunk_id, vector in vectores:
            archivo.write(_REGISTRO.pack(chunk_id, *vector))


# ---------------------------------------------------------------------------
# 1. Embeber
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ReporteEmbeber:
    nuevos: int = 0
    ya_estaban: int = 0
    esperas_por_cuota: int = 0
    segundos: float = 0.0


def tokens_estimados(textos: Sequence[str]) -> float:
    return sum(len(t.split()) for t in textos) * TOKENS_POR_PALABRA


def embeber(
    embedder: Embedder,
    chunks: Sequence[tuple[int, str]],
    cache: Path,
    *,
    lote: int = LOTE,
    tokens_por_minuto: float = TOKENS_POR_MINUTO,
    reintentos: int = 10,
    dormir: Callable[[float], None] = time.sleep,
    reloj: Callable[[], float] = time.monotonic,
    log: Callable[[str], None] = lambda _: None,
) -> ReporteEmbeber:
    """Embebe los ``(chunk_id, texto)`` que faltan en ``cache``, a ritmo de cuota."""

    reporte = ReporteEmbeber()
    inicio = reloj()
    guardados = leer_cache(cache)
    pendientes = [(i, t) for i, t in chunks if i not in guardados]
    reporte.ya_estaban = len(chunks) - len(pendientes)
    for desde in range(0, len(pendientes), lote):
        parte = pendientes[desde : desde + lote]
        textos = [t for _, t in parte]
        for intento in range(1, reintentos + 1):
            t0 = reloj()
            try:
                vectores = embedder.documentos(textos)
                break
            except LimiteDeUso:
                if intento == reintentos:
                    raise
                reporte.esperas_por_cuota += 1
                log(f"  limite de uso; se espera 60 s (intento {intento})")
                dormir(60.0)
        agregar_a_cache(cache, [(i, v) for (i, _), v in zip(parte, vectores, strict=True)])
        reporte.nuevos += len(parte)
        log(f"[{desde + len(parte)}/{len(pendientes)}] embebidos")
        # Ritmo: lo que este lote consume de la cuota por minuto.
        falta = tokens_estimados(textos) / (tokens_por_minuto / 60) - (reloj() - t0)
        if falta > 0 and desde + lote < len(pendientes):
            dormir(falta)
    reporte.segundos = reloj() - inicio
    return reporte


def chunks_del_motor(motor: ClienteMotor, tablas: Tablas) -> list[tuple[int, int, str]]:
    """``(id, paper_id, texto)`` de todos los chunks, en orden de id."""

    filas = motor.query(f"SELECT id, paper_id, texto FROM {tablas.chunks}").rows
    return sorted((int(i), int(p), str(t)) for i, p, t in filas)


# ---------------------------------------------------------------------------
# 2. Cargar en QuipuDB
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ReporteCarga:
    insertados: int = 0
    ya_estaban: int = 0
    sin_embedding: list[int] = field(default_factory=list)
    indice_creado: bool = False
    carga_s: float = 0.0
    indice_s: float = 0.0


def _indices(motor: ClienteMotor, tabla: str) -> set[str]:
    for info in motor.tables():
        if info["name"] == tabla:
            return {indice["name"] for indice in info["indexes"]}
    return set()


def preparar_tablas(motor: ClienteMotor, tablas: Tablas) -> None:
    """Crea ``chunk_vectores`` y el hash sobre ``chunks.id`` si faltan."""

    nombres = motor.table_names()
    if tablas.vectores not in nombres:
        motor.query(tablas.ddl_vectores())
    if tablas.chunks_por_id not in _indices(motor, tablas.chunks):
        motor.query(f"CREATE INDEX {tablas.chunks_por_id} ON {tablas.chunks} (id) USING HASH")


def insertar(
    motor: ClienteMotor,
    tablas: Tablas,
    filas: Sequence[tuple[int, int, Sequence[float]]],
    *,
    log: Callable[[str], None] = lambda _: None,
) -> int:
    """Inserta ``(chunk_id, paper_id, vector)`` por lotes de INSERT; si el HNSW
    ya existe, el DML lo mantiene fila por fila."""

    for desde in range(0, len(filas), INSERTS_POR_PETICION):
        parte = filas[desde : desde + INSERTS_POR_PETICION]
        motor.query(
            ";".join(
                f"INSERT INTO {tablas.vectores} VALUES ({c}, {p}, {literal_vector(v)})"
                for c, p, v in parte
            )
        )
        if (desde // INSERTS_POR_PETICION) % 20 == 0:
            log(f"[{desde + len(parte)}/{len(filas)}] insertados")
    return len(filas)


def cargar(
    motor: ClienteMotor,
    chunks: Sequence[tuple[int, int, str]],
    vectores: dict[int, Sequence[float]],
    *,
    tablas: Tablas = CORPUS,
    log: Callable[[str], None] = lambda _: None,
) -> ReporteCarga:
    reporte = ReporteCarga()
    preparar_tablas(motor, tablas)
    existentes = {int(f[0]) for f in motor.query(f"SELECT chunk_id FROM {tablas.vectores}").rows}
    reporte.ya_estaban = len(existentes)
    pendientes = []
    for chunk_id, paper_id, _ in chunks:
        if chunk_id in existentes:
            continue
        if chunk_id not in vectores:
            reporte.sin_embedding.append(chunk_id)
            continue
        pendientes.append((chunk_id, paper_id))

    inicio = time.perf_counter()
    reporte.insertados = insertar(
        motor, tablas, [(c, p, vectores[c]) for c, p in pendientes], log=log
    )
    reporte.carga_s = time.perf_counter() - inicio

    if tablas.hnsw not in _indices(motor, tablas.vectores):
        # Despues de cargar, no antes: construir el grafo una vez es mas barato
        # que mantenerlo fila por fila.
        inicio = time.perf_counter()
        motor.query(
            f"CREATE INDEX {tablas.hnsw} ON {tablas.vectores} (embedding) "
            "USING HNSW WITH METRIC=cosine"
        )
        reporte.indice_s = time.perf_counter() - inicio
        reporte.indice_creado = True
    return reporte


def lotes(secuencia: Sequence, n: int) -> Iterator[Sequence]:
    for desde in range(0, len(secuencia), n):
        yield secuencia[desde : desde + n]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Embeddings de los chunks y su carga en QuipuDB")
    parser.add_argument("paso", choices=["embeber", "cargar"])
    parser.add_argument("--limite", type=int, default=None, help="solo los primeros N chunks")
    args = parser.parse_args(argv)

    settings = load_settings()
    cache = ruta_cache(settings.gemini_embedding_model)
    with ClienteMotor.desde(settings) as motor:
        chunks = chunks_del_motor(motor, CORPUS)[: args.limite]
        if args.paso == "embeber":
            reporte = embeber(
                GeminiEmbedder.desde(settings), [(i, t) for i, _, t in chunks], cache, log=print
            )
            print(
                f"embebidos: {reporte.nuevos}, ya estaban: {reporte.ya_estaban}, "
                f"esperas por cuota: {reporte.esperas_por_cuota}, {reporte.segundos / 60:.1f} min"
            )
            return 0
        carga = cargar(motor, chunks, leer_cache(cache), log=print)
    print(
        f"insertados: {carga.insertados} en {carga.carga_s:.1f} s, ya estaban: {carga.ya_estaban}, "
        f"sin embedding: {len(carga.sin_embedding)}"
        + (f", indice HNSW creado en {carga.indice_s:.1f} s" if carga.indice_creado else "")
    )
    if carga.sin_embedding:
        print("  faltan embeddings: corre antes `python -m app.corpus.vectores embeber`")
    return 1 if carga.sin_embedding else 0


if __name__ == "__main__":
    sys.exit(main())
