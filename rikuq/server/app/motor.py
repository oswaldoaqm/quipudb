"""Literales SQL para hablar con el motor (#302).

El API del motor no tiene parametros preparados: el nombre de una persona
termina dentro de un ``INSERT``. Estos helpers arman los literales que el
lexer lee tal cual.
"""

from __future__ import annotations

def literal(texto: str) -> str:
    """Un literal de texto SQL que el lexer del motor lee tal cual.

    El API no tiene parametros preparados: el nombre de una persona termina
    dentro de un ``INSERT``. El lexer solo reconoce una cosa dentro de un
    literal, la comilla duplicada; ``;``, ``--`` y los saltos de linea ya no
    significan nada ahi. El caracter nulo se quita: no cabe en un VARCHAR.
    """

    return "'" + texto.replace("\x00", "").replace("'", "''") + "'"


def usa_tls(url: str) -> bool:
    """Si hay que cargar los certificados para hablar con ``url``.

    El motor suele estar en ``http://127.0.0.1``: sin TLS no hay nada que
    verificar, y cargar los certificados tarda segundos en algunas maquinas
    Windows.
    """

    return url.lower().startswith("https://")


__all__ = ["literal", "usa_tls"]
