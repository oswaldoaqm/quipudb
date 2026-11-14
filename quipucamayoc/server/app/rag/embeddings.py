"""Embeddings de Gemini para la busqueda vectorial (#252, ADR 0010).

Un chunk y una pregunta se convierten en vectores de ``DIMENSION`` numeros; el
coseno entre ellos mide cuanto se parecen en significado, aunque esten en
idiomas distintos. Los chunks se embeben como ``RETRIEVAL_DOCUMENT`` y las
preguntas como ``RETRIEVAL_QUERY``: el modelo los proyecta distinto a
proposito.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Protocol

from app.config import Settings
from app.corpus.esquema import DIMENSION_EMBEDDING
from app.rag.llm import TIMEOUT_S, LLMError, LLMNoConfigurado, _explicar

DIMENSION = DIMENSION_EMBEDDING
"""Lo que cabe: ``chunk_vectores`` guarda 768 float32 (3 072 bytes) mas dos INT,
dentro de los 4 087 bytes de un registro de heap. 1 024 ya no entraria."""


class LimiteDeUso(LLMError):
    """Gemini respondio 429: hay que esperar antes de seguir embebiendo."""


class Embedder(Protocol):
    modelo: str

    def documentos(self, textos: Sequence[str]) -> list[list[float]]: ...

    def consulta(self, texto: str) -> list[float]: ...


def normalizar(vector: Sequence[float]) -> list[float]:
    """El vector con norma 1. Con 768 dimensiones Gemini no lo normaliza."""

    norma = math.sqrt(sum(x * x for x in vector))
    if norma == 0 or not math.isfinite(norma):
        raise LLMError("Gemini devolvio un embedding nulo o invalido")
    return [x / norma for x in vector]


class GeminiEmbedder:
    def __init__(self, api_key: str, modelo: str, *, cliente: object | None = None) -> None:
        from google.genai import types

        self.modelo = modelo
        self._types = types
        if cliente is None:
            from google import genai

            # Sin reintentos del SDK: un 429 sube como LimiteDeUso y quien
            # embebe por lotes decide cuanto esperar.
            cliente = genai.Client(
                api_key=api_key,
                http_options=types.HttpOptions(
                    timeout=int(TIMEOUT_S * 1000),
                    retry_options=types.HttpRetryOptions(attempts=1),
                ),
            )
        self._cliente = cliente

    @classmethod
    def desde(cls, settings: Settings) -> GeminiEmbedder:
        if not settings.gemini_api_key:
            raise LLMNoConfigurado("falta GEMINI_API_KEY en quipucamayoc/.env")
        return cls(settings.gemini_api_key, settings.gemini_embedding_model)

    def _embeber(self, textos: Sequence[str], tarea: str) -> list[list[float]]:
        from google.genai import errors

        config = self._types.EmbedContentConfig(task_type=tarea, output_dimensionality=DIMENSION)
        try:
            respuesta = self._cliente.models.embed_content(
                model=self.modelo, contents=list(textos), config=config
            )
        except errors.APIError as error:
            if error.code == 429:
                raise LimiteDeUso(_explicar(error, self.modelo)) from error
            raise LLMError(_explicar(error, self.modelo)) from error
        except Exception as error:
            raise LLMError(f"Gemini no respondio ({type(error).__name__})") from error
        vectores = [list(e.values or []) for e in respuesta.embeddings or []]
        if len(vectores) != len(textos) or any(len(v) != DIMENSION for v in vectores):
            raise LLMError(
                f"Gemini devolvio {len(vectores)} embeddings para {len(textos)} textos"
            )
        return [normalizar(v) for v in vectores]

    def documentos(self, textos: Sequence[str]) -> list[list[float]]:
        return self._embeber(textos, "RETRIEVAL_DOCUMENT")

    def consulta(self, texto: str) -> list[float]:
        return self._embeber([texto], "RETRIEVAL_QUERY")[0]
