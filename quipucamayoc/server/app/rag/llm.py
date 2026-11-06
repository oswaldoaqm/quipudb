"""El LLM detras de una interfaz minima: hoy Gemini (ADR 0010, seccion 5).

El resto del RAG solo conoce ``LLM.generar``: las pruebas lo cambian por uno
falso, sin red ni clave, y cambiar de proveedor es escribir otra clase.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Protocol

from app.config import Settings

# Holgado a proposito: en Gemini 3 el razonamiento interno no se puede apagar
# y sus tokens cuentan contra este limite. Con 60, una reescritura de cuatro
# palabras volvia cortada (finish_reason MAX_TOKENS) despues de pensar 55.
MAX_TOKENS = 8192
TIMEOUT_S = 45
"""Por peticion. Un modelo saturado puede tardar mas que esto en rendirse: se
pasa al de respaldo en vez de esperarlo."""


class LLMError(RuntimeError):
    """Gemini no pudo responder. El mensaje es para mostrarlo tal cual."""


class LLMNoConfigurado(LLMError):
    pass


class LLM(Protocol):
    modelo: str
    """El modelo que respondio la ultima llamada."""

    def generar(self, prompt: str, *, sistema: str | None = None, temperatura: float = 0.2) -> str:
        """El texto que el modelo responde a ``prompt``."""
        ...


_SATURADO = {429, 500, 503}
"""Codigos con los que vale la pena probar otro modelo: saturacion o cuota."""

PAUSA_S = 120.0
"""Cuanto se salta un modelo que respondio saturado. Sin esto, cada pregunta
esperaria primero a que el modelo saturado falle (10 a 20 s) antes de pasar
al de respaldo."""


class GeminiLLM:
    """Gemini con modelos de respaldo.

    Si el modelo principal esta saturado o sin cuota, se prueban los de
    respaldo en orden. ``modelo`` dice cual respondio la ultima llamada.
    """

    def __init__(
        self,
        api_key: str,
        modelo: str,
        respaldos: tuple[str, ...] = (),
        *,
        timeout_s: float = TIMEOUT_S,
        cliente: object | None = None,
        reloj: Callable[[], float] = time.monotonic,
    ) -> None:
        from google.genai import types

        self.modelos = (modelo, *(m for m in respaldos if m != modelo))
        self.modelo = modelo
        self._reloj = reloj
        self._pausados: dict[str, float] = {}
        self._types = types
        if cliente is None:
            from google import genai

            # Dos intentos por modelo y no los cinco del SDK: con un modelo
            # saturado, cada reintento suma segundos que el de respaldo ahorra.
            cliente = genai.Client(
                api_key=api_key,
                http_options=types.HttpOptions(
                    timeout=int(timeout_s * 1000),
                    retry_options=types.HttpRetryOptions(attempts=2),
                ),
            )
        self._cliente = cliente

    @classmethod
    def desde(cls, settings: Settings) -> GeminiLLM:
        if not settings.gemini_api_key:
            raise LLMNoConfigurado("falta GEMINI_API_KEY en quipucamayoc/.env")
        return cls(settings.gemini_api_key, settings.gemini_model, settings.gemini_fallback_models)

    @classmethod
    def para_reescritura(cls, settings: Settings) -> GeminiLLM:
        """El modelo rapido de la reescritura, con el principal y sus respaldos detras."""

        if not settings.gemini_api_key:
            raise LLMNoConfigurado("falta GEMINI_API_KEY en quipucamayoc/.env")
        return cls(
            settings.gemini_api_key,
            settings.gemini_rewrite_model,
            (settings.gemini_model, *settings.gemini_fallback_models),
        )

    def generar(self, prompt: str, *, sistema: str | None = None, temperatura: float = 0.2) -> str:
        from google.genai import errors

        config = self._types.GenerateContentConfig(
            system_instruction=sistema, temperature=temperatura, max_output_tokens=MAX_TOKENS
        )
        orden = self._orden()
        for numero, modelo in enumerate(orden, start=1):
            try:
                respuesta = self._cliente.models.generate_content(
                    model=modelo, contents=prompt, config=config
                )
            except errors.APIError as error:
                if error.code in _SATURADO:
                    self._pausados[modelo] = self._reloj() + PAUSA_S
                    if numero < len(orden):
                        continue
                raise LLMError(_explicar(error, modelo)) from error
            except Exception as error:
                # Cortes de red y tiempos agotados llegan como excepciones de
                # httpx: se tratan como saturacion. El mensaje no lleva la
                # clave, solo el tipo.
                self._pausados[modelo] = self._reloj() + PAUSA_S
                if numero < len(orden):
                    continue
                raise LLMError(f"Gemini no respondio ({type(error).__name__})") from error
            texto = respuesta.text
            if not texto:
                motivo = respuesta.candidates[0].finish_reason if respuesta.candidates else None
                raise LLMError(f"Gemini devolvio una respuesta vacia (motivo: {motivo})")
            self.modelo = modelo
            return texto.strip()
        raise AssertionError("inalcanzable: hay al menos un modelo")

    def _orden(self) -> list[str]:
        """Los modelos disponibles primero, en su orden; los pausados al final.

        Si todos estan pausados se prueban igual: quiza alguno ya se libero.
        """

        ahora = self._reloj()
        libres = [m for m in self.modelos if self._pausados.get(m, 0.0) <= ahora]
        return libres + [m for m in self.modelos if m not in libres]


def _explicar(error: object, modelo: str) -> str:
    codigo = getattr(error, "code", None)
    detalle = str(getattr(error, "message", "") or "")[:300]
    if codigo == 400 and "API key" in detalle:
        return "la clave de Gemini no es valida: revisa GEMINI_API_KEY en quipucamayoc/.env"
    if codigo in (401, 403):
        return "la clave de Gemini no tiene permiso para este modelo"
    if codigo == 404:
        return f"el modelo {modelo!r} no esta disponible: {detalle}"
    if codigo == 429:
        return "se alcanzo el limite de uso de Gemini; espera un momento y vuelve a preguntar"
    if isinstance(codigo, int) and codigo >= 500:
        return f"Gemini no esta disponible ahora ({codigo}); vuelve a intentar"
    return f"Gemini rechazo la peticion ({codigo}): {detalle}"
