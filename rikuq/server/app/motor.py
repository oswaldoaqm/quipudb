"""Literales SQL para hablar con el motor (#302).

El API del motor no tiene parametros preparados: el nombre de una persona
termina dentro de un ``INSERT``. Estos helpers arman los literales que el
lexer lee tal cual, incluido el VECTOR del embedding facial.
"""

from __future__ import annotations

from collections.abc import Sequence

def literal(texto: str) -> str:
    """Un literal de texto SQL que el lexer del motor lee tal cual.

    El API no tiene parametros preparados: el nombre de una persona termina
    dentro de un ``INSERT``. El lexer solo reconoce una cosa dentro de un
    literal, la comilla duplicada; ``;``, ``--`` y los saltos de linea ya no
    significan nada ahi. El caracter nulo se quita: no cabe en un VARCHAR.
    """

    return "'" + texto.replace("\x00", "").replace("'", "''") + "'"


def literal_vector(vector: Sequence[float]) -> str:
    """``VECTOR(...)`` en decimal fijo: el lexer no lee notacion cientifica.

    Sin esto, una componente como ``3.4e-05`` saldria en notacion cientifica y
    el motor la rechazaria. Los vectores de #305 estan normalizados (cada
    componente entre -1 y 1), asi que 7 decimales conservan la precision de un
    float32.
    """

    return "VECTOR(" + ", ".join(f"{float(x):.7f}" for x in vector) + ")"


def usa_tls(url: str) -> bool:
    """Si hay que cargar los certificados para hablar con ``url``.

    El motor suele estar en ``http://127.0.0.1``: sin TLS no hay nada que
    verificar, y cargar los certificados tarda segundos en algunas maquinas
    Windows.
    """

    return url.lower().startswith("https://")


__all__ = ["literal", "literal_vector", "usa_tls"]
