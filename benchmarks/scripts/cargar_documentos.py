"""Carga un corpus de texto (issue #171) en una tabla Heap de QuipuDB con indice invertido.

La lectura y validacion de los CSV no necesita los bindings; la carga si, y
falla explicitamente si no estan compilados. Las comparaciones del 2.3.2 (#172,
#173, #174) reutilizan `leer_documentos`, `leer_consultas` y `leer_relevancia`
para que TF-IDF, BM25 y el GIN de PostgreSQL reciban exactamente los mismos
documentos y consultas.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib
import io
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

if __package__:
    from .generar_documentos import (
        CABECERA_CONSULTAS,
        CABECERA_DOCUMENTOS,
        CABECERA_RELEVANCIA,
        LARGO_TEXTO,
        LONGITUDES,
        TAMANOS,
    )
else:
    from generar_documentos import (
        CABECERA_CONSULTAS,
        CABECERA_DOCUMENTOS,
        CABECERA_RELEVANCIA,
        LARGO_TEXTO,
        LONGITUDES,
        TAMANOS,
    )

TABLA = "documentos"
INDICE = "documentos_texto"


@dataclass(frozen=True)
class DatasetDocumentos:
    ruta: Path
    sha256: str
    # (id, texto), en el orden del CSV.
    registros: tuple[tuple[int, str], ...]


@dataclass(frozen=True)
class ConsultaTexto:
    id: int
    longitud: str  # "1", "3" o "5+"
    texto: str


def _filas(ruta: Path, cabecera: tuple[str, ...], generador: str) -> Iterator[tuple[int, list]]:
    """(numero de linea, fila) de un CSV con la cabecera esperada."""
    try:
        contenido = ruta.read_text(encoding="utf-8")
    except FileNotFoundError as error:
        raise ValueError(f"archivo inexistente: {ruta}; ejecuta {generador}") from error
    lector = csv.reader(io.StringIO(contenido, newline=""), strict=True)
    if next(lector, None) != list(cabecera):
        raise ValueError("la cabecera debe ser " + ",".join(cabecera))
    for numero, fila in enumerate(lector, start=2):
        if len(fila) != len(cabecera):
            raise ValueError(f"fila {numero}: se requieren {len(cabecera)} columnas")
        yield numero, fila


_GENERADOR = "benchmarks/scripts/generar_documentos.py"


def leer_documentos(ruta: Path, tamano: int | None = None) -> DatasetDocumentos:
    """Lee y valida todo el corpus antes de cargar o medir nada."""
    if tamano is not None and tamano not in TAMANOS:
        raise ValueError(f"tamano no soportado: {tamano}; usa uno de {TAMANOS}")
    registros = []
    try:
        for numero, (identificador, _, texto) in _filas(ruta, CABECERA_DOCUMENTOS, _GENERADOR):
            # Los ids son 1..N en orden: comprobarlo asi tambien descarta duplicados.
            if identificador != str(numero - 1):
                raise ValueError(f"fila {numero}: se esperaba el id {numero - 1}")
            if not texto or len(texto.encode("utf-8")) > LARGO_TEXTO or "\0" in texto:
                raise ValueError(f"fila {numero}: texto incompatible con VARCHAR({LARGO_TEXTO})")
            registros.append((numero - 1, texto))
        if tamano is not None and len(registros) != tamano:
            raise ValueError(f"se esperaban {tamano} documentos, se encontraron {len(registros)}")
        sha256 = hashlib.sha256(ruta.read_bytes()).hexdigest()
    except (ValueError, UnicodeError, csv.Error) as error:
        raise ValueError(f"dataset invalido {ruta}: {error}") from error
    return DatasetDocumentos(ruta.resolve(), sha256, tuple(registros))


def leer_consultas(ruta: Path) -> tuple[ConsultaTexto, ...]:
    """Las consultas en el orden del CSV: tres por pregunta, de 1, 3 y 5+ palabras."""
    consultas = []
    try:
        for numero, (identificador, longitud, _, texto) in _filas(
            ruta, CABECERA_CONSULTAS, _GENERADOR
        ):
            if identificador != str(numero - 1):
                raise ValueError(f"fila {numero}: se esperaba el id {numero - 1}")
            if longitud not in LONGITUDES:
                raise ValueError(f"fila {numero}: longitud {longitud!r} fuera de {LONGITUDES}")
            # Sin comilla simple, la consulta va tal cual dentro de MATCH(col, '...').
            if not texto or "'" in texto:
                raise ValueError(f"fila {numero}: consulta vacia o con comilla simple")
            consultas.append(ConsultaTexto(numero - 1, longitud, texto))
    except (ValueError, UnicodeError, csv.Error) as error:
        raise ValueError(f"consultas invalidas {ruta}: {error}") from error
    return tuple(consultas)


def leer_relevancia(ruta: Path) -> dict[int, frozenset[int]]:
    """Id de consulta -> ids de los documentos relevantes del corpus del mismo tamano."""
    relevantes: dict[int, set[int]] = {}
    try:
        for numero, (consulta, documento) in _filas(ruta, CABECERA_RELEVANCIA, _GENERADOR):
            try:
                relevantes.setdefault(int(consulta), set()).add(int(documento))
            except ValueError as error:
                raise ValueError(f"fila {numero}: ids no numericos") from error
    except (ValueError, UnicodeError, csv.Error) as error:
        raise ValueError(f"relevancia invalida {ruta}: {error}") from error
    return {consulta: frozenset(docs) for consulta, docs in relevantes.items()}


def importar_bindings():
    """El modulo nativo, o un error que explica como obtenerlo."""
    try:
        return importlib.import_module("quipudb_native")
    except ImportError as error:
        raise RuntimeError(
            "no se pueden cargar los bindings quipudb_native; compila con "
            "-DQUIPUDB_BUILD_PYTHON=ON y agrega build-py/bindings a PYTHONPATH"
        ) from error


def esquema_documentos(nativo):
    """id INT clave, texto VARCHAR(256)."""
    return nativo.Schema(
        TABLA,
        [
            nativo.Column("id", nativo.DataType.INT),
            nativo.Column("texto", nativo.DataType.VARCHAR, LARGO_TEXTO),
        ],
        key_column=0,
    )


def opciones_ingles(nativo):
    """Stopwords y stemmer ingleses: Quora esta en ingles.

    Son las de to_tsvector('english', ...), asi que el GIN de #174 ve los
    mismos terminos.
    """
    opciones = nativo.TokenizerOptions()
    opciones.stopword_languages = [nativo.TextLanguage.ENGLISH]
    opciones.stemmer = nativo.TextLanguage.ENGLISH
    return opciones


def cargar_quipudb(nativo, db, dataset: DatasetDocumentos, *, con_indice: bool = True):
    """Crea la tabla Heap, inserta en el orden del CSV y, si se pide, construye el
    indice invertido sobre `texto` con las opciones inglesas. Devuelve (tabla,
    indice o None).

    `create_index` construye con las opciones por omision, que son las del
    espanol; `build` lo reconstruye en ingles. Quien mida el tiempo de
    construccion tiene que medir solo el `build`.
    """
    tabla = db.create_table(esquema_documentos(nativo), nativo.kind.HEAP)
    for registro in dataset.registros:
        tabla.insert(list(registro))
    indice = None
    if con_indice:
        indice = db.create_index(TABLA, INDICE, "texto", nativo.kind.INVERTED_INDEX)
        indice.build(opciones_ingles(nativo))
    db.flush()
    return tabla, indice


def main() -> None:
    """Carga un corpus en un catalogo nuevo e informa cantidades, tiempos y tamanos."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("dataset", type=Path, help="documentos_N.csv de generar_documentos.py")
    parser.add_argument(
        "--catalogo",
        type=Path,
        required=True,
        help="carpeta nueva o vacia donde se crean el catalogo y los archivos",
    )
    parser.add_argument("--sin-indice", action="store_true", help="no construir el indice")
    args = parser.parse_args()

    dataset = leer_documentos(args.dataset)
    args.catalogo.mkdir(parents=True, exist_ok=True)
    if any(args.catalogo.iterdir()):
        parser.error(f"{args.catalogo} no esta vacia; no se reutilizan catalogos")
    nativo = importar_bindings()
    db = nativo.Database(args.catalogo / "catalogo.txt")

    inicio = time.perf_counter()
    tabla, indice = cargar_quipudb(nativo, db, dataset, con_indice=not args.sin_indice)
    segundos = time.perf_counter() - inicio

    print(f"{dataset.ruta}: {len(dataset.registros)} documentos, sha256 {dataset.sha256}")
    print(f"tabla {TABLA}: {len(tabla)} filas")
    if indice is not None:
        print(f"indice {INDICE} (inverted_index): {indice.terms} terminos")
    print(f"carga completa en {segundos:.3f} s")
    for archivo in sorted(args.catalogo.iterdir()):
        print(f"  {archivo.name}: {archivo.stat().st_size} bytes")


if __name__ == "__main__":
    main()
