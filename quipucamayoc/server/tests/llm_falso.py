"""Un LLM de mentira para las pruebas: sin red, sin clave y sin gastar cuota."""

from __future__ import annotations

from collections.abc import Callable

from app.rag.llm import LLMError


class LLMFalso:
    modelo = "falso-1"

    def __init__(
        self,
        reescritura: str | Exception = "graph index",
        respuesta: str | Callable[[str], str] | Exception = "Los grafos ganan [1].",
    ) -> None:
        self._reescritura = reescritura
        self._respuesta = respuesta
        self.llamadas: list[tuple[str, str | None]] = []

    def generar(self, prompt: str, *, sistema: str | None = None, temperatura: float = 0.2) -> str:
        self.llamadas.append((prompt, sistema))
        es_reescritura = sistema is not None and "buscador BM25" in sistema
        valor = self._reescritura if es_reescritura else self._respuesta
        if isinstance(valor, Exception):
            raise valor
        return valor(prompt) if callable(valor) else valor


__all__ = ["LLMError", "LLMFalso"]


class EmbedderFalso:
    """Embeddings de mentira pero con sentido: cada palabra suma en una casilla
    elegida por un hash estable. Dos textos con palabras en comun quedan
    cerca en coseno, que es lo que las pruebas necesitan."""

    modelo = "embedding-falso"

    def __init__(self, sinonimos: dict[str, str] | None = None) -> None:
        # Para simular que el embedding cruza idiomas: "grafo" cuenta como "graph".
        self.sinonimos = sinonimos or {}
        self.llamadas: list[tuple[str, int]] = []

    def _vector(self, texto: str) -> list[float]:
        import hashlib
        import re

        from app.rag.embeddings import DIMENSION, normalizar

        vector = [0.0] * DIMENSION
        for palabra in re.findall(r"[a-záéíóúñ]+", texto.lower()):
            palabra = self.sinonimos.get(palabra, palabra)
            casilla = int.from_bytes(hashlib.md5(palabra.encode()).digest()[:4], "little")
            vector[casilla % DIMENSION] += 1.0
        if not any(vector):
            vector[0] = 1.0
        return normalizar(vector)

    def documentos(self, textos):
        self.llamadas.append(("documentos", len(textos)))
        return [self._vector(t) for t in textos]

    def consulta(self, texto):
        self.llamadas.append(("consulta", 1))
        return self._vector(texto)
