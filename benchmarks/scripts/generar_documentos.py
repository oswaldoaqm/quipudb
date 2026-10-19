"""Genera los corpus de texto de 1k, 10k y 100k, sus consultas y sus juicios de relevancia (#171).

Los documentos, las consultas y los juicios salen de BEIR Quora (Thakur et al.,
2021): 522 931 preguntas de Quora y, para 10 000 de ellas, cuales de las demas
son la misma pregunta escrita de otra forma. Se elige un corpus con juicios
hechos por personas porque sin ellos no hay Precision@10 ni Recall@10 (#173).

Casi no hay preguntas de una o de tres palabras (2 y 76 de 10 000), asi que las
consultas cortas se derivan de una pregunta larga quedandose con sus palabras
mas discriminativas, las de menor frecuencia de documento en todo Quora. Las
tres consultas de una misma pregunta comparten sus juicios de relevancia: al
comparar longitudes solo cambia la longitud.

El zip se descarga una vez a benchmarks/datasets (fuera de Git) y se comprueba
su SHA-256 antes de usarlo.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import re
import shutil
import urllib.request
import zipfile
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

SEMILLA = 20260906
TAMANOS = (1_000, 10_000, 100_000)
SALIDA_PREDETERMINADA = Path(__file__).resolve().parents[1] / "datasets"

URL_QUORA = "https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/datasets/quora.zip"
SHA256_QUORA = "56aacd9dcc4d9c093b63f175afdda0e21cbc8442ecf6c717d09de7b358d77531"
ZIP_PREDETERMINADO = SALIDA_PREDETERMINADA / "quora.zip"

CABECERA_DOCUMENTOS = ("id", "quora_id", "texto")
CABECERA_CONSULTAS = ("id", "longitud", "pregunta", "texto")
CABECERA_RELEVANCIA = ("consulta", "documento")

# Bytes de la columna VARCHAR de la tabla. Cubre el 99,8 % de Quora; los
# registros son de largo fijo, asi que subirlo agranda cada pagina del Heap.
LARGO_TEXTO = 256
# Preguntas base. Cada una da tres consultas, una por longitud.
PREGUNTAS = 100
LONGITUDES = ("1", "3", "5+")
MIN_PALABRAS_LARGA = 5
# Una palabra que solo aparece en un documento de Quora no puede encontrar otro.
DF_MINIMO = 2

# La lista inglesa de Snowball, la misma que aplica el core (text_stopwords.cpp)
# y to_tsvector('english', ...). Una consulta corta hecha de stopwords quedaria
# vacia al tokenizarla.
_LISTA_INGLES = """
i me my myself we our ours ourselves you your yours yourself yourselves he him
his himself she her hers herself it its itself they them their theirs themselves
what which who whom this that these those am is are was were be been being have
has had having do does did doing a an the and but if or because as until while
of at by for with about against between into through during before after above
below to from up down in out on off over under again further then once here
there when where why how all any both each few more most other some such no nor
not only own same so than too very s t can will just don should now
"""
STOPWORDS_INGLES = frozenset(_LISTA_INGLES.split())

_PALABRA = re.compile(r"[^\W_]+")


def palabras(texto: str) -> list[str]:
    """Las palabras del texto en minusculas, en orden y con repeticiones."""
    return _PALABRA.findall(texto.lower())


@dataclass(frozen=True)
class Quora:
    # quora_id -> texto de la pregunta, para todo el corpus.
    documentos: dict[str, str]
    # quora_id de la consulta -> texto, solo las del split test.
    consultas: dict[str, str]
    # quora_id de la consulta -> quora_id de sus documentos relevantes.
    relevantes: dict[str, frozenset[str]]


@dataclass(frozen=True)
class Consulta:
    id: int
    longitud: str
    pregunta: str  # quora_id de la pregunta de la que sale
    texto: str


def sha256_de(ruta: Path) -> str:
    resumen = hashlib.sha256()
    with ruta.open("rb") as archivo:
        for bloque in iter(lambda: archivo.read(1 << 20), b""):
            resumen.update(bloque)
    return resumen.hexdigest()


def obtener_zip(ruta: Path, sha256: str = SHA256_QUORA) -> Path:
    """El zip de BEIR Quora, descargandolo si no esta; falla si no es el esperado."""
    if not ruta.exists():
        ruta.parent.mkdir(parents=True, exist_ok=True)
        parcial = ruta.with_name(ruta.name + ".parcial")
        with (
            urllib.request.urlopen(URL_QUORA, timeout=60) as respuesta,
            parcial.open("wb") as archivo,
        ):
            shutil.copyfileobj(respuesta, archivo)
        parcial.replace(ruta)
    obtenido = sha256_de(ruta)
    if obtenido != sha256:
        raise ValueError(
            f"{ruta}: SHA-256 {obtenido}, se esperaba {sha256}; borralo para descargarlo de nuevo"
        )
    return ruta


def _lineas_json(zip_quora: zipfile.ZipFile, nombre: str) -> Iterator[dict]:
    with zip_quora.open(nombre) as archivo:
        for linea in archivo:
            if linea.strip():
                yield json.loads(linea)


def leer_quora(ruta: Path) -> Quora:
    """Lee corpus, consultas y juicios del split test sin descomprimir el zip."""
    with zipfile.ZipFile(ruta) as zip_quora:
        documentos = {
            fila["_id"]: fila["text"] for fila in _lineas_json(zip_quora, "quora/corpus.jsonl")
        }
        todas = {
            fila["_id"]: fila["text"] for fila in _lineas_json(zip_quora, "quora/queries.jsonl")
        }
        relevantes: dict[str, set[str]] = {}
        lineas = zip_quora.read("quora/qrels/test.tsv").decode("utf-8").splitlines()
        for linea in lineas[1:]:
            consulta, documento, puntaje = linea.split("\t")
            # BEIR evalua Quora ignorando que una pregunta se encuentre a si misma.
            if int(puntaje) > 0 and documento != consulta:
                relevantes.setdefault(consulta, set()).add(documento)
    return Quora(
        documentos,
        {consulta: todas[consulta] for consulta in relevantes},
        {consulta: frozenset(docs) for consulta, docs in relevantes.items()},
    )


def frecuencias_de_documento(textos: Iterable[str]) -> Counter[str]:
    """En cuantos textos aparece cada palabra."""
    df: Counter[str] = Counter()
    for texto in textos:
        df.update(set(palabras(texto)))
    return df


def cabe_en_la_tabla(texto: str) -> bool:
    """Si el texto entra en VARCHAR(LARGO_TEXTO) y tiene alguna palabra."""
    return len(texto.encode("utf-8")) <= LARGO_TEXTO and "\0" not in texto and bool(palabras(texto))


def palabras_discriminativas(texto: str, df: Counter[str]) -> list[str]:
    """Las palabras de la pregunta que sirven de consulta, de la mas rara a la mas comun.

    Sin repetidas, sin stopwords, de al menos dos caracteres y presentes en al
    menos DF_MINIMO documentos de Quora. A igual frecuencia, la que aparece antes.
    """
    vistas: dict[str, int] = {}
    for posicion, palabra in enumerate(palabras(texto)):
        if (
            palabra not in vistas
            and palabra not in STOPWORDS_INGLES
            and len(palabra) >= 2
            and df[palabra] >= DF_MINIMO
        ):
            vistas[palabra] = posicion
    return sorted(vistas, key=lambda palabra: (df[palabra], vistas[palabra]))


def consultas_de(texto: str, df: Counter[str]) -> tuple[str, str, str] | None:
    """Las consultas de 1, 3 y 5+ palabras de una pregunta, o None si no da para tres.

    Las cortas conservan el orden en que las palabras aparecen en la pregunta.
    Todas van en minusculas, sin puntuacion: asi caben en un literal SQL sin
    escapar comillas y QuipuDB y PostgreSQL reciben exactamente el mismo texto.
    """
    todas = palabras(texto)
    candidatas = palabras_discriminativas(texto, df)
    if len(todas) < MIN_PALABRAS_LARGA or len(candidatas) < 3:
        return None
    tres = set(candidatas[:3])
    en_orden = [palabra for palabra in dict.fromkeys(todas) if palabra in tres]
    return candidatas[0], " ".join(en_orden), " ".join(todas)


def elegir_preguntas(quora: Quora, df: Counter[str], cantidad: int) -> list[str]:
    """Las preguntas base, en un orden al azar reproducible.

    Se descarta una pregunta si no da para las tres consultas o si alguno de sus
    relevantes no cabe en la tabla. Tambien si choca con una ya elegida: las
    preguntas elegidas salen del corpus (se encontrarian a si mismas), asi que
    ninguna puede ser relevante para otra.
    """
    rng = random.Random(SEMILLA)
    candidatas = sorted(quora.relevantes, key=int)
    rng.shuffle(candidatas)
    elegidas: list[str] = []
    ocupados: set[str] = set()
    for pregunta in candidatas:
        relevantes = quora.relevantes[pregunta]
        if (
            consultas_de(quora.consultas[pregunta], df) is None
            or not all(cabe_en_la_tabla(quora.documentos[doc]) for doc in relevantes)
            or pregunta in ocupados
            or not relevantes.isdisjoint(elegidas)
        ):
            continue
        elegidas.append(pregunta)
        ocupados |= relevantes
        if len(elegidas) == cantidad:
            return elegidas
    raise ValueError(f"solo hay {len(elegidas)} preguntas utilizables; se pidieron {cantidad}")


def armar_consultas(quora: Quora, df: Counter[str], preguntas: list[str]) -> list[Consulta]:
    consultas = []
    for pregunta in preguntas:
        textos = consultas_de(quora.consultas[pregunta], df)
        assert textos is not None  # elegir_preguntas ya lo comprobo
        for longitud, texto in zip(LONGITUDES, textos, strict=True):
            consultas.append(Consulta(len(consultas) + 1, longitud, pregunta, texto))
    return consultas


def orden_de_relleno(quora: Quora, excluidos: set[str]) -> list[str]:
    """Los documentos que completan los corpus, en un orden al azar fijo.

    Cada tamano toma un prefijo de esta misma lista: el corpus de 1k esta
    contenido en el de 10k y este en el de 100k.
    """
    rng = random.Random(SEMILLA)
    relleno = sorted(
        (
            doc
            for doc, texto in quora.documentos.items()
            if doc not in excluidos and cabe_en_la_tabla(texto)
        ),
        key=int,
    )
    rng.shuffle(relleno)
    return relleno


def armar_corpus(tamano: int, relevantes: set[str], relleno: list[str]) -> list[str]:
    """Los quora_id del corpus en el orden de sus ids 1..N.

    Los relevantes se mezclan con el relleno: si fueran los primeros ids, un
    empate de puntaje (que se rompe por id) los favoreceria.
    """
    faltan = tamano - len(relevantes)
    if faltan < 0 or faltan > len(relleno):
        raise ValueError(f"no se puede armar un corpus de {tamano} documentos")
    corpus = sorted(relevantes | set(relleno[:faltan]), key=int)
    # Un generador nuevo por tamano: el orden no depende de que otros tamanos se generaron.
    random.Random(SEMILLA + tamano).shuffle(corpus)
    return corpus


def _escribir_csv(ruta: Path, cabecera: tuple[str, ...], filas: Iterable[tuple]) -> Path:
    with ruta.open("w", encoding="utf-8", newline="") as archivo:
        escritor = csv.writer(archivo, lineterminator="\n")
        escritor.writerow(cabecera)
        escritor.writerows(filas)
    return ruta


def generar(
    quora: Quora, salida: Path, tamanos: Iterable[int], preguntas: int = PREGUNTAS
) -> list[Path]:
    """Escribe consultas.csv y, por tamano, documentos_N.csv y relevancia_N.csv."""
    df = frecuencias_de_documento(quora.documentos.values())
    elegidas = elegir_preguntas(quora, df, preguntas)
    consultas = armar_consultas(quora, df, elegidas)
    relevantes = set().union(*(quora.relevantes[pregunta] for pregunta in elegidas))
    relleno = orden_de_relleno(quora, relevantes | set(elegidas))

    salida.mkdir(parents=True, exist_ok=True)
    rutas = [
        _escribir_csv(
            salida / "consultas.csv",
            CABECERA_CONSULTAS,
            ((c.id, c.longitud, c.pregunta, c.texto) for c in consultas),
        )
    ]
    for tamano in tamanos:
        corpus = armar_corpus(tamano, relevantes, relleno)
        rutas.append(
            _escribir_csv(
                salida / f"documentos_{tamano}.csv",
                CABECERA_DOCUMENTOS,
                (
                    (posicion, doc, quora.documentos[doc])
                    for posicion, doc in enumerate(corpus, start=1)
                ),
            )
        )
        id_de = {doc: posicion for posicion, doc in enumerate(corpus, start=1)}
        rutas.append(
            _escribir_csv(
                salida / f"relevancia_{tamano}.csv",
                CABECERA_RELEVANCIA,
                (
                    (consulta.id, documento)
                    for consulta in consultas
                    for documento in sorted(
                        id_de[doc] for doc in quora.relevantes[consulta.pregunta]
                    )
                ),
            )
        )
    return rutas


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
    parser.add_argument(
        "--zip",
        type=Path,
        default=ZIP_PREDETERMINADO,
        help="quora.zip de BEIR; si no existe se descarga ahi (por defecto: benchmarks/datasets)",
    )
    parser.add_argument(
        "--sha256",
        default=SHA256_QUORA,
        help="SHA-256 esperado del zip (por defecto: el de BEIR Quora)",
    )
    args = parser.parse_args()
    quora = leer_quora(obtener_zip(args.zip, args.sha256))
    for ruta in generar(quora, args.salida, args.tamanos):
        with ruta.open(encoding="utf-8") as archivo:
            print(f"{ruta}: {sum(1 for _ in archivo) - 1} filas")


if __name__ == "__main__":
    main()
