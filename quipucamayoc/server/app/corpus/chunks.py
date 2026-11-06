"""Division de un documento en chunks (#242, ADR 0010).

Un chunk es lo que indexa BM25 y lo que lee el LLM: tiene que ser lo bastante
largo para contener una idea completa y lo bastante corto para que el puntaje
no se diluya. El ADR fija:

- 300 palabras de objetivo, nunca menos de 200 ni mas de 500, salvo el ultimo
  chunk de un paper corto;
- 50 palabras de solapamiento, para que una respuesta no quede partida entre
  dos chunks sin que ninguno la tenga entera;
- cortar en el fin de oracion mas cercano al objetivo;
- a lo sumo 4 000 bytes UTF-8, lo que cabe en ``chunks.texto VARCHAR(4000)``.

Es determinista: el mismo documento da siempre los mismos chunks.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

OBJETIVO = 300
MINIMO = 200
MAXIMO = 500
SOLAPAMIENTO = 50
MAX_BYTES = 4000

# Abreviaturas que terminan en punto sin terminar la oracion. En minusculas.
_ABREVIATURAS = frozenset(
    {"e.g.", "i.e.", "et", "al.", "fig.", "figs.", "eq.", "eqs.", "sec.", "ref.", "vs.",
     "etc.", "cf.", "no.", "vol.", "pp.", "approx.", "resp.", "dr.", "mr.", "ms."}
)
_FIN_DE_ORACION = re.compile(r"[.?!][\"')\]]*$")
_INICIO_DE_ORACION = re.compile(r"^[\"'(\[]*[A-Z0-9]")


@dataclass(frozen=True, slots=True)
class Chunk:
    orden: int
    """Posicion dentro del paper, desde 0."""
    pagina: int
    """Pagina (desde 1) donde empieza el chunk."""
    texto: str

    @property
    def palabras(self) -> int:
        return len(self.texto.split())


@dataclass(frozen=True, slots=True)
class _Palabra:
    texto: str
    pagina: int


def dividir(
    paginas: Sequence[str],
    *,
    objetivo: int = OBJETIVO,
    minimo: int = MINIMO,
    maximo: int = MAXIMO,
    solapamiento: int = SOLAPAMIENTO,
    max_bytes: int = MAX_BYTES,
) -> list[Chunk]:
    """Los chunks del texto de ``paginas`` (una cadena por pagina, en orden)."""

    if not 0 < minimo <= objetivo <= maximo:
        raise ValueError("se necesita 0 < minimo <= objetivo <= maximo")
    if not 0 <= solapamiento < minimo:
        raise ValueError("el solapamiento tiene que ser menor que el minimo")

    palabras = [
        _Palabra(palabra, numero)
        for numero, pagina in enumerate(paginas, start=1)
        for palabra in pagina.split()
    ]
    fines = _fines_de_oracion(palabras)

    chunks: list[Chunk] = []
    inicio = 0
    while inicio < len(palabras):
        restantes = len(palabras) - inicio
        if restantes <= maximo and (restantes <= objetivo or restantes - objetivo < minimo):
            # Lo que queda cabe en un chunk, y partirlo dejaria una cola de
            # menos de `minimo` palabras: va entero.
            fin = len(palabras)
        else:
            fin = _elegir_fin(fines, inicio, objetivo, minimo, maximo)
        fin = _ajustar_a_bytes(palabras, inicio, fin, max_bytes)
        texto = _texto(palabras[inicio:fin], max_bytes)
        chunks.append(Chunk(len(chunks), palabras[inicio].pagina, texto))
        if fin >= len(palabras):
            break
        # El siguiente empieza `solapamiento` palabras antes, pero siempre avanza.
        inicio = max(fin - solapamiento, inicio + 1)
    return chunks


def _fines_de_oracion(palabras: Sequence[_Palabra]) -> list[int]:
    """Posiciones ``i`` tales que una oracion termina justo antes de la palabra i."""

    fines = []
    for i in range(1, len(palabras)):
        anterior, siguiente = palabras[i - 1].texto, palabras[i].texto
        if (
            _FIN_DE_ORACION.search(anterior)
            and anterior.lower() not in _ABREVIATURAS
            and _INICIO_DE_ORACION.match(siguiente)
        ):
            fines.append(i)
    return fines


def _elegir_fin(fines: Sequence[int], inicio: int, objetivo: int, minimo: int, maximo: int) -> int:
    """El fin de oracion entre minimo y maximo mas cercano al objetivo.

    Sin ninguno (una tabla, una formula larga), se corta en el objetivo.
    """

    candidatos = [fin for fin in fines if inicio + minimo <= fin <= inicio + maximo]
    if not candidatos:
        return inicio + objetivo
    # A igual distancia gana el mas corto: deja mas texto para el siguiente.
    return min(candidatos, key=lambda fin: (abs(fin - (inicio + objetivo)), fin))


def _ajustar_a_bytes(palabras: Sequence[_Palabra], inicio: int, fin: int, max_bytes: int) -> int:
    """Achica ``fin`` hasta que el texto quepa en ``max_bytes``; minimo una palabra."""

    while fin - inicio > 1 and _bytes(palabras[inicio:fin]) > max_bytes:
        fin -= 1
    return fin


def _bytes(palabras: Sequence[_Palabra]) -> int:
    return sum(len(p.texto.encode("utf-8")) for p in palabras) + max(len(palabras) - 1, 0)


def _texto(palabras: Sequence[_Palabra], max_bytes: int) -> str:
    texto = " ".join(p.texto for p in palabras)
    datos = texto.encode("utf-8")
    if len(datos) <= max_bytes:
        return texto
    # Una sola "palabra" mas larga que el tope (una URL, una tabla sin
    # espacios): se recorta sin partir un caracter.
    return datos[:max_bytes].decode("utf-8", errors="ignore")
