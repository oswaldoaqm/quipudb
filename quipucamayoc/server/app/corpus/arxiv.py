"""Seleccion y descarga de los papers de arXiv del corpus (#240).

Dos pasos, para que todo el equipo tenga el mismo corpus:

1. ``seleccionar`` consulta la API de arXiv con consultas fijas sobre los temas
   del curso y escribe la lista en ``quipucamayoc/corpus/papers.tsv``, que se
   versiona. Las busquedas de arXiv cambian con el tiempo; la lista no.
2. ``descargar`` lee esa lista, trae los metadatos y los PDFs a
   ``quipucamayoc/datos/`` (fuera del repositorio) y salta lo que ya esta.

Uso, desde ``quipucamayoc/server``::

    python -m app.corpus.arxiv seleccionar
    python -m app.corpus.arxiv descargar

arXiv pide no pasar de una peticion cada 3 segundos y limita a quien insiste
(responde 429 o 503): el cliente espacia todas las peticiones y reintenta con
espera creciente.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Self

import httpx

API_URL = "https://export.arxiv.org/api/query"
PDF_URL = "https://arxiv.org/pdf/{id}"
USER_AGENT = "quipucamayoc/0.1 (proyecto academico, UTEC)"

QUIPUCAMAYOC = Path(__file__).resolve().parents[3]
LISTA = QUIPUCAMAYOC / "corpus" / "papers.tsv"
DATOS = QUIPUCAMAYOC / "datos"

_NS = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}

# Los temas del curso, de la Parte 1 a la 4, mas RAG: preguntas sobre
# cualquier parte del proyecto encuentran papers que las respondan.
CONSULTAS: tuple[str, ...] = (
    'all:"approximate nearest neighbor search"',
    'all:"vector database"',
    'all:"product quantization"',
    'all:"retrieval augmented generation"',
    'all:"dense passage retrieval"',
    'all:"BM25" AND cat:cs.IR',
    'all:"inverted index" AND cat:cs.IR',
    'all:"learned index"',
    'all:"R-tree" AND cat:cs.DB',
    'all:"query optimization" AND cat:cs.DB',
    'all:"concurrency control" AND cat:cs.DB',
    '(all:"external sorting" OR all:"B-tree") AND cat:cs.DB',
)
POR_CONSULTA = 15

# Papers que la demo necesita aunque una busqueda no los traiga: los de las
# preguntas del enunciado ("ventajas de HNSW sobre IVF") y los clasicos.
SEMILLAS: tuple[str, ...] = (
    "1603.09320",  # HNSW
    "1702.08734",  # Faiss en GPU
    "2401.08281",  # The Faiss library
    "1807.05614",  # ANN-Benchmarks
    "2005.11401",  # RAG
    "2004.04906",  # Dense Passage Retrieval
    "1712.01208",  # The Case for Learned Index Structures
    "2310.11703",  # A Comprehensive Survey on Vector Database
)


@dataclass(frozen=True, slots=True)
class Paper:
    arxiv_id: str
    """Con version (``1603.09320v4``): el PDF descargado es siempre el mismo."""
    titulo: str
    autores: tuple[str, ...]
    anio: int
    categoria: str
    abstract: str
    licencia: str | None = None

    @property
    def id_base(self) -> str:
        """Sin version: lo que se compara para no repetir un paper."""
        return self.arxiv_id.rsplit("v", 1)[0] if _tiene_version(self.arxiv_id) else self.arxiv_id

    @property
    def archivo(self) -> str:
        """Nombre del PDF: los ids viejos (``cs/0101001``) llevan una barra."""
        return self.arxiv_id.replace("/", "_") + ".pdf"

    @property
    def url_pdf(self) -> str:
        return PDF_URL.format(id=self.arxiv_id)


def _tiene_version(arxiv_id: str) -> bool:
    cabeza, _, version = arxiv_id.rpartition("v")
    return bool(cabeza) and version.isdigit()


def _texto(nodo: ET.Element | None) -> str:
    return " ".join((nodo.text or "").split()) if nodo is not None else ""


def parse_feed(xml: str) -> list[Paper]:
    """Los papers de una respuesta Atom de la API de arXiv."""

    raiz = ET.fromstring(xml)
    papers = []
    for entrada in raiz.findall("a:entry", _NS):
        url = _texto(entrada.find("a:id", _NS))
        if "/abs/" not in url:  # una entrada de error de la API
            continue
        categoria = entrada.find("arxiv:primary_category", _NS)
        papers.append(
            Paper(
                arxiv_id=url.split("/abs/", 1)[1],
                titulo=_texto(entrada.find("a:title", _NS)),
                autores=tuple(
                    _texto(autor.find("a:name", _NS)) for autor in entrada.findall("a:author", _NS)
                ),
                anio=int(_texto(entrada.find("a:published", _NS))[:4]),
                categoria=categoria.get("term", "") if categoria is not None else "",
                abstract=_texto(entrada.find("a:summary", _NS)),
                licencia=_texto(entrada.find("arxiv:license", _NS)) or None,
            )
        )
    return papers


class ArxivError(RuntimeError):
    pass


class ArxivClient:
    """Cliente de arXiv que respeta su limite de peticiones.

    ``sleep`` y ``clock`` se inyectan para probar la espera sin esperar.
    """

    def __init__(
        self,
        *,
        transport: httpx.BaseTransport | None = None,
        intervalo_s: float = 3.0,
        reintentos: int = 6,
        timeout_s: float = 60.0,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        log: Callable[[str], None] = lambda _: None,
    ) -> None:
        self._http = httpx.Client(
            transport=transport,
            timeout=timeout_s,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        )
        self._intervalo = intervalo_s
        self._reintentos = reintentos
        self._sleep = sleep
        self._clock = clock
        self._log = log
        self._ultima: float | None = None

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _esperar_turno(self) -> None:
        if self._ultima is not None:
            falta = self._intervalo - (self._clock() - self._ultima)
            if falta > 0:
                self._sleep(falta)
        self._ultima = self._clock()

    def get(self, url: str, params: dict[str, str | int] | None = None) -> httpx.Response:
        """GET con espacio entre peticiones y reintentos ante 429, 5xx y cortes."""

        espera = self._intervalo
        for intento in range(1, self._reintentos + 1):
            self._esperar_turno()
            try:
                respuesta = self._http.get(url, params=params)
            except httpx.TransportError as error:
                motivo = type(error).__name__
            else:
                if respuesta.status_code == 200:
                    return respuesta
                if respuesta.status_code != 429 and respuesta.status_code < 500:
                    raise ArxivError(f"{url} respondio {respuesta.status_code}")
                motivo = f"HTTP {respuesta.status_code}"
                pedida = respuesta.headers.get("Retry-After", "")
                if pedida.isdigit():
                    espera = max(espera, float(pedida))
            if intento == self._reintentos:
                raise ArxivError(f"{url} fallo {intento} veces; la ultima: {motivo}")
            self._log(f"  {motivo}; reintento {intento} en {espera:.0f} s")
            self._sleep(espera)
            espera = min(espera * 2, 120.0)
        raise AssertionError("inalcanzable")

    def buscar(self, consulta: str, cantidad: int) -> list[Paper]:
        respuesta = self.get(
            API_URL,
            {"search_query": consulta, "max_results": cantidad, "sortBy": "relevance"},
        )
        return parse_feed(respuesta.text)

    def metadatos(self, ids: Sequence[str], lote: int = 50) -> list[Paper]:
        """Los papers de esos ids, en el mismo orden. Los que no existen no salen."""

        papers: list[Paper] = []
        for inicio in range(0, len(ids), lote):
            parte = list(ids[inicio : inicio + lote])
            respuesta = self.get(
                API_URL, {"id_list": ",".join(parte), "max_results": len(parte)}
            )
            papers.extend(parse_feed(respuesta.text))
        return papers


def seleccionar(
    cliente: ArxivClient,
    consultas: Iterable[str] = CONSULTAS,
    por_consulta: int = POR_CONSULTA,
    semillas: Sequence[str] = SEMILLAS,
) -> list[Paper]:
    """Semillas primero, despues cada consulta en orden, sin repetir papers."""

    elegidos: dict[str, Paper] = {}
    for paper in cliente.metadatos(semillas):
        elegidos.setdefault(paper.id_base, paper)
    for consulta in consultas:
        for paper in cliente.buscar(consulta, por_consulta):
            elegidos.setdefault(paper.id_base, paper)
    return list(elegidos.values())


def escribir_lista(papers: Iterable[Paper], ruta: Path = LISTA) -> None:
    """La lista versionada: id con version, anio, categoria y titulo."""

    ruta.parent.mkdir(parents=True, exist_ok=True)
    lineas = ["# arxiv_id\tanio\tcategoria\ttitulo"]
    lineas += [f"{p.arxiv_id}\t{p.anio}\t{p.categoria}\t{p.titulo}" for p in papers]
    ruta.write_text("\n".join(lineas) + "\n", encoding="utf-8", newline="\n")


def leer_lista(ruta: Path = LISTA) -> list[str]:
    ids = []
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        if linea.strip() and not linea.startswith("#"):
            ids.append(linea.split("\t", 1)[0].strip())
    return ids


@dataclass(frozen=True, slots=True)
class Descarga:
    nuevos: int
    existentes: int
    fallidos: tuple[tuple[str, str], ...]


def _pdf_valido(ruta: Path) -> bool:
    if not ruta.is_file() or ruta.stat().st_size < 1024:
        return False
    with ruta.open("rb") as archivo:
        return archivo.read(5) == b"%PDF-"


def descargar(
    cliente: ArxivClient,
    ids: Sequence[str],
    destino: Path = DATOS,
    log: Callable[[str], None] = lambda _: None,
) -> Descarga:
    """Trae metadatos y PDFs a ``destino``; salta los PDFs que ya estan.

    Los metadatos se escriben en ``destino/metadatos.jsonl``, un paper por
    linea y en el orden de la lista. Un PDF que falla no detiene a los demas.
    """

    (destino / "pdf").mkdir(parents=True, exist_ok=True)
    papers = cliente.metadatos(ids)
    faltan = sorted(set(ids) - {p.arxiv_id for p in papers})
    with (destino / "metadatos.jsonl").open("w", encoding="utf-8", newline="\n") as salida:
        for paper in papers:
            salida.write(json.dumps(asdict(paper), ensure_ascii=False) + "\n")

    nuevos = existentes = 0
    fallidos = [(arxiv_id, "arXiv no devolvio sus metadatos") for arxiv_id in faltan]
    for numero, paper in enumerate(papers, start=1):
        ruta = destino / "pdf" / paper.archivo
        if _pdf_valido(ruta):
            existentes += 1
            continue
        log(f"[{numero}/{len(papers)}] {paper.arxiv_id} {paper.titulo[:60]}")
        try:
            respuesta = cliente.get(paper.url_pdf)
            if not respuesta.content.startswith(b"%PDF-"):
                raise ArxivError("la respuesta no es un PDF")
        except ArxivError as error:
            fallidos.append((paper.arxiv_id, str(error)))
            continue
        # Se escribe al lado y se renombra: un corte a mitad no deja un PDF
        # truncado que la siguiente corrida daria por bueno.
        temporal = ruta.with_suffix(".part")
        temporal.write_bytes(respuesta.content)
        temporal.replace(ruta)
        nuevos += 1
    return Descarga(nuevos, existentes, tuple(fallidos))


def leer_metadatos(destino: Path = DATOS) -> Iterator[Paper]:
    with (destino / "metadatos.jsonl").open(encoding="utf-8") as entrada:
        for linea in entrada:
            datos = json.loads(linea)
            datos["autores"] = tuple(datos["autores"])
            yield Paper(**datos)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    sub = parser.add_subparsers(dest="paso", required=True)
    sub.add_parser("seleccionar", help=f"consulta arXiv y escribe {LISTA.name}")
    bajar = sub.add_parser("descargar", help="trae metadatos y PDFs de la lista")
    bajar.add_argument("--destino", type=Path, default=DATOS)
    args = parser.parse_args(argv)

    with ArxivClient(log=print) as cliente:
        if args.paso == "seleccionar":
            papers = seleccionar(cliente)
            escribir_lista(papers)
            print(f"{len(papers)} papers en {LISTA}")
            return 0
        resultado = descargar(cliente, leer_lista(), args.destino, log=print)
    print(
        f"PDFs nuevos: {resultado.nuevos}, ya estaban: {resultado.existentes}, "
        f"fallidos: {len(resultado.fallidos)}"
    )
    for arxiv_id, motivo in resultado.fallidos:
        print(f"  {arxiv_id}: {motivo}")
    return 1 if resultado.fallidos else 0


if __name__ == "__main__":
    sys.exit(main())
