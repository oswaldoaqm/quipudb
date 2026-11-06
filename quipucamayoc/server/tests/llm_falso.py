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
