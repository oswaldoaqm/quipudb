"""Texto limpio de un PDF, pagina por pagina (#241).

Lo que sale de aqui es lo que se divide en chunks (#242) y lo que el LLM
termina citando, asi que se limpia lo que estorba a BM25 y al lector:

- palabras cortadas con guion al final de una linea (``inde-`` + ``xing``),
- cabeceras y pies que se repiten en las paginas (titulo de la revista,
  numero de pagina, el sello lateral de arXiv),
- ligaduras tipograficas (``ﬁ`` -> ``fi``) y caracteres de control,
- la seccion de referencias, opcionalmente: sus nombres y titulos atraen
  coincidencias de BM25 que no responden ninguna pregunta (ADR 0010).
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PdfReadError

# Lineas de los bordes de cada pagina que se miran para detectar cabeceras y
# pies: los PDFs de dos columnas ponen ahi revista, autores y numero.
_BORDE = 3
# Una linea de borde que aparece en al menos esta fraccion de las paginas es
# una cabecera o un pie.
_REPETIDA = 0.5

_SELLO_ARXIV = re.compile(r"^arXiv:\S+\s+\[[^\]]+\]\s+\d{1,2}\s+\w{3}\s+\d{4}$")
_NUMERO_DE_PAGINA = re.compile(r"^(page\s+)?\d{1,4}(\s+of\s+\d{1,4})?$", re.IGNORECASE)
_REFERENCIAS = re.compile(
    r"^(\d+\.?\s*|[IVX]+\.?\s*)?(references|bibliography|referencias)\s*$", re.IGNORECASE
)
_CORTE_CON_GUION = re.compile(r"(\w)-\n\s*([a-z])")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class PdfError(ValueError):
    """El PDF no se pudo leer o no tiene texto: no hay nada que indexar."""


@dataclass(frozen=True, slots=True)
class DocumentoPdf:
    paginas: tuple[str, ...]
    """El texto de cada pagina, en orden; una pagina vacia queda como ``""``."""

    @property
    def palabras(self) -> int:
        return sum(len(pagina.split()) for pagina in self.paginas)


def extraer(ruta: Path, *, quitar_referencias: bool = True) -> DocumentoPdf:
    """El texto limpio de ``ruta``. Lanza ``PdfError`` si no hay texto que sacar."""

    try:
        lector = PdfReader(ruta)
        if lector.is_encrypted:
            # Muchos PDFs "protegidos" solo tienen permisos y se abren con la
            # clave vacia; si no, no hay forma de leerlos.
            try:
                lector.decrypt("")
                crudas = [pagina.extract_text() or "" for pagina in lector.pages]
            # pypdf lanza de todo al descifrar: cualquier fallo es que no se pudo.
            except Exception as error:
                raise PdfError(f"{ruta.name}: el PDF esta protegido con clave") from error
        else:
            crudas = [pagina.extract_text() or "" for pagina in lector.pages]
    except PdfError:
        raise
    except (PdfReadError, OSError, ValueError, KeyError) as error:
        raise PdfError(f"{ruta.name}: no es un PDF legible ({error})") from error

    documento = limpiar(crudas, quitar_referencias=quitar_referencias)
    if documento.palabras == 0:
        raise PdfError(f"{ruta.name}: el PDF no tiene texto (¿es un escaneo?)")
    return documento


def limpiar(paginas: list[str], *, quitar_referencias: bool = True) -> DocumentoPdf:
    """La limpieza sin pypdf, para probarla con texto escrito a mano."""

    lineas_por_pagina = [_lineas(_normalizar(pagina)) for pagina in paginas]
    repetidas = _lineas_repetidas(lineas_por_pagina)
    limpias = [
        [
            linea
            for indice, linea in enumerate(lineas)
            if not _es_ruido(linea, indice, len(lineas), repetidas)
        ]
        for lineas in lineas_por_pagina
    ]
    if quitar_referencias:
        limpias = _sin_referencias(limpias)
    return DocumentoPdf(tuple(_unir(lineas) for lineas in limpias))


def _normalizar(texto: str) -> str:
    # NFKC separa las ligaduras (ﬁ, ﬂ) y unifica espacios raros.
    texto = unicodedata.normalize("NFKC", texto).replace("\r\n", "\n").replace("\r", "\n")
    return _CONTROL.sub(" ", texto)


def _lineas(texto: str) -> list[str]:
    return [" ".join(linea.split()) for linea in texto.split("\n") if linea.strip()]


def _clave(linea: str) -> str:
    # El numero de pagina cambia en cada cabecera: se compara sin digitos.
    return re.sub(r"\d+", "#", linea.lower())


def _lineas_repetidas(paginas: list[list[str]]) -> set[str]:
    if len(paginas) < 3:
        return set()
    conteo: Counter[str] = Counter()
    for lineas in paginas:
        bordes = lineas[:_BORDE] + lineas[-_BORDE:]
        conteo.update({_clave(linea) for linea in bordes})
    minimo = max(2, int(len(paginas) * _REPETIDA))
    return {clave for clave, veces in conteo.items() if veces >= minimo}


def _es_ruido(linea: str, indice: int, total: int, repetidas: set[str]) -> bool:
    en_borde = indice < _BORDE or indice >= total - _BORDE
    if not en_borde:
        return False
    return (
        _clave(linea) in repetidas
        or bool(_NUMERO_DE_PAGINA.match(linea))
        or bool(_SELLO_ARXIV.match(linea))
    )


def _sin_referencias(paginas: list[list[str]]) -> list[list[str]]:
    """Corta desde el ultimo titulo de referencias en la segunda mitad del paper.

    El ultimo, porque "References" puede aparecer antes en el cuerpo; en la
    segunda mitad, para no vaciar un paper que lo menciona en la introduccion.
    """

    for numero in range(len(paginas) - 1, len(paginas) // 2 - 1, -1):
        lineas = paginas[numero]
        for indice in range(len(lineas) - 1, -1, -1):
            if _REFERENCIAS.match(lineas[indice]):
                return [*paginas[:numero], lineas[:indice]] + [[] for _ in paginas[numero + 1 :]]
    return paginas


def _unir(lineas: list[str]) -> str:
    texto = _CORTE_CON_GUION.sub(r"\1\2", "\n".join(lineas))
    return " ".join(texto.split())
