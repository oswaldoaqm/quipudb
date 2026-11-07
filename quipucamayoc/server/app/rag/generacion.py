"""Generacion: la respuesta de Gemini con citas a los papers (#246).

El recorrido completo de una pregunta (ADR 0010, secciones 4 y 6):

1. Gemini reescribe la pregunta como palabras clave en ingles, el idioma del
   corpus. Si falla, se busca con la pregunta original.
2. La recuperacion (#245) trae los chunks de QuipuDB. Si la consulta
   reescrita no encuentra nada, se intenta con la original.
3. Sin chunks no se llama al LLM: se responde que los documentos no tienen
   la informacion.
4. Con chunks, Gemini responde citando ``[n]``; las citas se convierten en
   papers y las que no corresponden a ningun chunk enviado se descartan.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from app.corpus.carga import CORPUS
from app.corpus.esquema import Tablas
from app.motor import ClienteMotor
from app.rag.embeddings import Embedder
from app.rag.llm import LLM, LLMError
from app.rag.recuperacion import Evidencia, K, Modo, Ranking, Recuperacion, recuperar

MAX_CONTEXTO = 24_000
"""Caracteres de chunks que entran al prompt (unos 6 000 tokens): sobra para
ocho chunks de 300 palabras, y deja afuera a los menos relevantes si no caben."""

SIN_INFORMACION = (
    "No encontré esa información en los papers de la colección. Prueba con otras "
    "palabras, o con los términos técnicos en inglés."
)

_SISTEMA_REESCRITURA = (
    "Conviertes preguntas en consultas para un buscador BM25 sobre papers academicos "
    "en ingles. Responde con una sola linea: las palabras clave en ingles, sin comillas, "
    "sin operadores y sin explicaciones. Conserva siglas y nombres propios."
)

_SISTEMA_RESPUESTA = """Eres Quipucamayoc, un asistente que responde preguntas sobre papers \
academicos usando solo los fragmentos que se te entregan.

Reglas:
- Responde en el mismo idioma de la pregunta.
- Usa solo la informacion de los fragmentos. Si no alcanza para responder, dilo.
- Cita cada afirmacion con el numero de su fragmento entre corchetes, por ejemplo [2] \
o [1][3]. No cites numeros que no esten en la lista.
- Se conciso: unos pocos parrafos, sin repetir la pregunta."""

_CITA = re.compile(r"\[(\d+(?:\s*[,;]\s*\d+)*)\]")


@dataclass(frozen=True, slots=True)
class Cita:
    numero: int
    evidencia: Evidencia


@dataclass(frozen=True, slots=True)
class Tiempos:
    reescritura_ms: float = 0.0
    recuperacion_ms: float = 0.0
    generacion_ms: float = 0.0
    embedding_ms: float = 0.0
    """El embedding de la pregunta (modos vectores e hibrido, #252)."""


@dataclass(frozen=True, slots=True)
class Respuesta:
    pregunta: str
    consulta: str
    """Lo que se busco con MATCH: la reescritura o, si no hubo, la pregunta."""
    texto: str
    citas: tuple[Cita, ...]
    """Las citas validas de ``texto``, por numero y sin repetir."""
    evidencias: tuple[Evidencia, ...]
    """Los chunks que se le enviaron al LLM, en el orden de su numero."""
    recuperacion: Recuperacion
    tiempos: Tiempos
    modelo: str | None
    """``None`` si no se llamo al LLM para responder."""
    citas_descartadas: tuple[int, ...] = ()


def reescribir(llm: LLM, pregunta: str) -> str | None:
    """La pregunta como palabras clave en ingles, o ``None`` si no se pudo."""

    try:
        texto = llm.generar(pregunta, sistema=_SISTEMA_REESCRITURA, temperatura=0.0)
    except LLMError:
        return None
    linea = next((linea.strip() for linea in texto.splitlines() if linea.strip()), "")
    linea = linea.strip("\"'`").strip()
    return linea or None


def contexto(evidencias: Sequence[Evidencia], max_caracteres: int = MAX_CONTEXTO) -> list[Evidencia]:
    """Las evidencias que caben en el prompt, en orden de relevancia.

    Si no caben todas, se descartan las del final: las de menor puntaje.
    Siempre entra al menos una.
    """

    elegidas: list[Evidencia] = []
    total = 0
    for evidencia in evidencias:
        largo = len(evidencia.fragmento.texto)
        if elegidas and total + largo > max_caracteres:
            break
        elegidas.append(evidencia)
        total += largo
    return elegidas


def prompt(pregunta: str, evidencias: Sequence[Evidencia]) -> str:
    bloques = []
    for numero, e in enumerate(evidencias, start=1):
        p = e.paper
        bloques.append(
            f"[{numero}] {p.autores} ({p.anio}). {p.titulo}. arXiv:{p.arxiv_id}, "
            f"pagina {e.fragmento.pagina}.\n{e.fragmento.texto}"
        )
    return "Fragmentos:\n\n" + "\n\n".join(bloques) + f"\n\nPregunta: {pregunta}"


def depurar_citas(texto: str, total: int) -> tuple[str, list[int], list[int]]:
    """Separa las citas validas (1..total) de las inventadas y quita estas del texto.

    Devuelve el texto limpio, los numeros validos en orden de aparicion y los
    descartados.
    """

    validas: list[int] = []
    descartadas: list[int] = []

    def reemplazar(coincidencia: re.Match[str]) -> str:
        numeros = [int(n) for n in re.split(r"\s*[,;]\s*", coincidencia.group(1))]
        buenos = [n for n in numeros if 1 <= n <= total]
        for n in numeros:
            destino = validas if 1 <= n <= total else descartadas
            if n not in destino:
                destino.append(n)
        return "".join(f"[{n}]" for n in buenos)

    limpio = _CITA.sub(reemplazar, texto)
    limpio = re.sub(r"[ \t]+([.,;:])", r"\1", limpio)  # el espacio que dejo una cita borrada
    return limpio, validas, descartadas


def responder(
    motor: ClienteMotor,
    llm: LLM,
    pregunta: str,
    *,
    k: int = K,
    ranking: Ranking = "BM25",
    reescribir_consulta: bool = True,
    llm_reescritura: LLM | None = None,
    tablas: Tablas = CORPUS,
    avance: Callable[[str, object], None] | None = None,
    modo: Modo = "texto",
    embedder: Embedder | None = None,
) -> Respuesta:
    """``llm_reescritura`` es el modelo de la reescritura; sin el, se usa ``llm``.

    En los modos ``vectores`` e ``hibrido``, ``embedder`` convierte la pregunta
    *original* en un vector: el embedding cruza idiomas, asi que no hace falta
    reescribirla. En ``vectores`` ni siquiera se reescribe; en ``hibrido`` la
    reescritura alimenta a BM25 y el embedding al HNSW.

    ``avance`` se llama al terminar cada etapa, antes de la generacion que es
    la lenta: ``("consulta", str)`` y ``("recuperacion", Recuperacion)``. Con
    eso la interfaz muestra las fuentes mientras Gemini todavia escribe.
    """

    avisar = avance or (lambda _etapa, _datos: None)
    pregunta = pregunta.strip()
    if not pregunta:
        raise ValueError("la pregunta esta vacia")

    if modo != "texto" and embedder is None:
        raise ValueError(f"el modo {modo} necesita un embedder")

    inicio = time.perf_counter()
    reescritor = llm_reescritura or llm
    reescribe = reescribir_consulta and modo != "vectores"
    consulta = (reescribir(reescritor, pregunta) if reescribe else None) or pregunta
    t_reescritura = (time.perf_counter() - inicio) * 1000

    vector = None
    t_embedding = 0.0
    if modo != "texto":
        inicio = time.perf_counter()
        vector = embedder.consulta(pregunta)  # type: ignore[union-attr]
        t_embedding = (time.perf_counter() - inicio) * 1000
    avisar("consulta", consulta)

    def buscar(texto: str) -> Recuperacion:
        return recuperar(
            motor, texto, k=k, ranking=ranking, tablas=tablas, modo=modo, vector=vector
        )

    recuperacion = buscar(consulta)
    t_recuperacion = recuperacion.tiempo_ms
    if not recuperacion.evidencias and consulta != pregunta and modo == "texto":
        # La reescritura pudo perder un termino que el corpus si tiene.
        consulta = pregunta
        recuperacion = buscar(consulta)
        t_recuperacion += recuperacion.tiempo_ms
    avisar("recuperacion", recuperacion)

    if not recuperacion.evidencias:
        return Respuesta(
            pregunta,
            consulta,
            SIN_INFORMACION,
            (),
            (),
            recuperacion,
            Tiempos(t_reescritura, t_recuperacion, 0.0, t_embedding),
            None,
        )

    evidencias = contexto(recuperacion.evidencias)
    inicio = time.perf_counter()
    crudo = llm.generar(prompt(pregunta, evidencias), sistema=_SISTEMA_RESPUESTA)
    t_generacion = (time.perf_counter() - inicio) * 1000

    texto, validas, descartadas = depurar_citas(crudo, len(evidencias))
    return Respuesta(
        pregunta=pregunta,
        consulta=consulta,
        texto=texto,
        citas=tuple(Cita(n, evidencias[n - 1]) for n in validas),
        evidencias=tuple(evidencias),
        recuperacion=recuperacion,
        tiempos=Tiempos(t_reescritura, t_recuperacion, t_generacion, t_embedding),
        modelo=llm.modelo,
        citas_descartadas=tuple(descartadas),
    )
