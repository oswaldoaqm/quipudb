"""Generacion de la respuesta con citas (#246), con un LLM falso."""

from __future__ import annotations

import pytest

from app.config import Settings
from app.rag.generacion import (
    SIN_INFORMACION,
    contexto,
    depurar_citas,
    prompt,
    reescribir,
    responder,
)
from app.rag.llm import GeminiLLM, LLMError, LLMNoConfigurado, _explicar
from app.rag.recuperacion import Evidencia, Fragmento, PaperRef
from tests.llm_falso import LLMFalso


def _evidencia(numero: int, texto: str = "Graphs beat inverted files.") -> Evidencia:
    return Evidencia(
        Fragmento(numero * 10, numero, 0, 3, texto, 10.0 - numero),
        PaperRef(numero, f"2401.0000{numero}v1", f"Paper {numero}", "Ada Lovelace", 2020, "u"),
    )


# ---------------------------------------------------------------------------
# Piezas sueltas
# ---------------------------------------------------------------------------


def test_las_citas_validas_quedan_y_las_inventadas_se_quitan() -> None:
    texto, validas, descartadas = depurar_citas(
        "HNSW gana [2]. IVF comprime [1, 3]; ver tambien [9]. Otra vez [2][7].", total=3
    )

    assert texto == "HNSW gana [2]. IVF comprime [1][3]; ver tambien. Otra vez [2]."
    assert validas == [2, 1, 3]
    assert descartadas == [9, 7]


def test_un_numero_entre_corchetes_que_no_es_cita_no_rompe_nada() -> None:
    texto, validas, _ = depurar_citas("Sin citas.", total=2)

    assert (texto, validas) == ("Sin citas.", [])


def test_el_contexto_deja_afuera_lo_menos_relevante_si_no_cabe() -> None:
    evidencias = [_evidencia(1, "a" * 100), _evidencia(2, "b" * 100), _evidencia(3, "c" * 100)]

    assert contexto(evidencias, max_caracteres=250) == evidencias[:2]
    assert contexto(evidencias[:1], max_caracteres=10) == evidencias[:1]  # al menos una


def test_el_prompt_numera_los_fragmentos_con_su_referencia() -> None:
    texto = prompt("¿Que gana?", [_evidencia(1), _evidencia(2)])

    assert texto.startswith("Fragmentos:\n\n[1] Ada Lovelace (2020). Paper 1. arXiv:2401.00001v1, pagina 3.")
    assert "\n[2] Ada Lovelace (2020). Paper 2." in texto
    assert texto.endswith("Pregunta: ¿Que gana?")


@pytest.mark.parametrize(
    ("salida", "esperada"),
    [
        ("HNSW advantages over IVF", "HNSW advantages over IVF"),
        ('"HNSW vs IVF"\n', "HNSW vs IVF"),
        ("\n\nHNSW IVF recall\nexplicacion de mas", "HNSW IVF recall"),
        ("   ", None),
        (LLMError("sin cuota"), None),
    ],
)
def test_reescribir_toma_la_primera_linea_o_se_rinde(salida, esperada) -> None:
    assert reescribir(LLMFalso(reescritura=salida), "¿Ventajas de HNSW?") == esperada


@pytest.mark.parametrize(
    ("codigo", "mensaje", "esperado"),
    [
        (400, "API key not valid. Please pass a valid API key.", "la clave de Gemini no es valida"),
        (403, "forbidden", "no tiene permiso"),
        (404, "models/x is not found", "el modelo 'gemini-x' no esta disponible"),
        (429, "quota", "limite de uso"),
        (503, "overloaded", "no esta disponible ahora (503)"),
    ],
)
def test_los_errores_de_gemini_se_explican(codigo, mensaje, esperado) -> None:
    class Error:
        code = codigo
        message = mensaje

    assert esperado in _explicar(Error(), "gemini-x")


def test_sin_clave_no_se_crea_el_cliente_de_gemini() -> None:
    with pytest.raises(LLMNoConfigurado, match="GEMINI_API_KEY"):
        GeminiLLM.desde(Settings())


# ---------------------------------------------------------------------------
# Todo el recorrido, contra el motor real y con el LLM falso
# ---------------------------------------------------------------------------


def test_responde_con_la_consulta_reescrita_y_cita_los_papers(corpus) -> None:
    motor, tablas = corpus
    llm = LLMFalso(
        reescritura="graph greedy routing layers",
        respuesta="El grafo baja por capas [1] y rara vez falla [12].",
    )

    r = responder(motor, llm, "¿Cómo busca un grafo jerárquico?", tablas=tablas)

    assert r.consulta == "graph greedy routing layers"
    assert r.texto == "El grafo baja por capas [1] y rara vez falla."
    assert [c.numero for c in r.citas] == [1]
    assert r.citas[0].evidencia.paper.arxiv_id == "2401.00001v1"
    assert r.citas_descartadas == (12,)
    assert r.modelo == "falso-1"
    assert r.tiempos.recuperacion_ms > 0
    prompt_enviado, sistema = llm.llamadas[-1]
    assert "Pregunta: ¿Cómo busca un grafo jerárquico?" in prompt_enviado
    assert "[1] Ada Lovelace (2020). Graph indexes." in prompt_enviado
    assert "Responde en el mismo idioma de la pregunta" in sistema


def test_si_la_reescritura_falla_busca_con_la_pregunta(corpus) -> None:
    motor, tablas = corpus
    llm = LLMFalso(reescritura=LLMError("sin cuota"))

    r = responder(motor, llm, "inverted file centroids", tablas=tablas)

    assert r.consulta == "inverted file centroids"
    assert r.evidencias[0].paper.arxiv_id == "2401.00002v1"


def test_si_la_reescritura_no_encuentra_nada_prueba_con_la_pregunta(corpus) -> None:
    motor, tablas = corpus
    llm = LLMFalso(reescritura="helicopter submarine")

    r = responder(motor, llm, "histograms selectivity", tablas=tablas)

    assert r.consulta == "histograms selectivity"
    assert r.evidencias[0].paper.arxiv_id == "2401.00003v1"


def test_sin_evidencias_no_se_llama_al_llm_para_responder(corpus) -> None:
    motor, tablas = corpus
    llm = LLMFalso(reescritura="helicopter", respuesta=AssertionError("no debia llamarse"))

    r = responder(motor, llm, "submarine", tablas=tablas)

    assert (r.texto, r.citas, r.modelo) == (SIN_INFORMACION, (), None)
    assert len(llm.llamadas) == 1  # solo la reescritura


def test_un_error_del_llm_al_responder_sube_tal_cual(corpus) -> None:
    motor, tablas = corpus
    llm = LLMFalso(respuesta=LLMError("se alcanzo el limite de uso de Gemini"))

    with pytest.raises(LLMError, match="limite de uso"):
        responder(motor, llm, "graph", tablas=tablas)


def test_una_pregunta_vacia_es_error() -> None:
    with pytest.raises(ValueError, match="vacia"):
        responder(None, LLMFalso(), "   ")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Modelos de respaldo
# ---------------------------------------------------------------------------


class _ClienteGemini:
    """Imita ``genai.Client``: cada modelo responde lo que diga ``guion``."""

    def __init__(self, guion: dict[str, object]) -> None:
        self.guion = guion
        self.pedidos: list[str] = []
        self.models = self

    def generate_content(self, *, model: str, contents: str, config: object) -> object:
        self.pedidos.append(model)
        resultado = self.guion[model]
        if isinstance(resultado, Exception):
            raise resultado

        class Respuesta:
            text = resultado
            candidates = ()

        return Respuesta()


def _api_error(codigo: int) -> Exception:
    errors = pytest.importorskip("google.genai.errors")
    return errors.APIError(codigo, {"error": {"code": codigo, "message": "high demand"}})


def test_un_modelo_saturado_pasa_al_de_respaldo() -> None:
    cliente = _ClienteGemini({"principal": _api_error(503), "respaldo": "ok"})
    llm = GeminiLLM("clave", "principal", ("respaldo",), cliente=cliente)

    assert llm.generar("hola") == "ok"
    assert llm.modelo == "respaldo"
    assert cliente.pedidos == ["principal", "respaldo"]


def test_sin_cuota_tambien_pasa_al_respaldo_pero_una_clave_mala_no() -> None:
    cliente = _ClienteGemini({"principal": _api_error(429), "respaldo": "ok"})
    assert GeminiLLM("clave", "principal", ("respaldo",), cliente=cliente).generar("x") == "ok"

    cliente = _ClienteGemini({"principal": _api_error(403), "respaldo": "ok"})
    with pytest.raises(LLMError, match="no tiene permiso"):
        GeminiLLM("clave", "principal", ("respaldo",), cliente=cliente).generar("x")
    assert cliente.pedidos == ["principal"]


def test_si_todos_estan_saturados_se_explica_con_el_ultimo() -> None:
    cliente = _ClienteGemini({"principal": _api_error(503), "respaldo": _api_error(503)})

    with pytest.raises(LLMError, match=r"no esta disponible ahora \(503\)"):
        GeminiLLM("clave", "principal", ("respaldo", "principal"), cliente=cliente).generar("x")
    assert cliente.pedidos == ["principal", "respaldo"]  # sin repetir el principal


def test_los_respaldos_se_leen_del_entorno() -> None:
    from app.config import DEFAULT_FALLBACK_MODELS, load_settings

    assert load_settings(None, environ={}).gemini_fallback_models == DEFAULT_FALLBACK_MODELS
    leidos = load_settings(None, environ={"GEMINI_FALLBACK_MODELS": "a, b ,"})
    assert leidos.gemini_fallback_models == ("a", "b")
    assert load_settings(None, environ={"GEMINI_FALLBACK_MODELS": ""}).gemini_fallback_models == ()


def test_un_modelo_saturado_se_salta_un_rato_y_despues_se_vuelve_a_probar() -> None:
    from app.rag.llm import PAUSA_S

    ahora = [0.0]
    cliente = _ClienteGemini({"principal": _api_error(503), "respaldo": "ok"})
    llm = GeminiLLM("clave", "principal", ("respaldo",), cliente=cliente, reloj=lambda: ahora[0])

    llm.generar("1")
    llm.generar("2")
    assert cliente.pedidos == ["principal", "respaldo", "respaldo"]

    ahora[0] = PAUSA_S + 1
    cliente.guion["principal"] = "ya libre"
    assert llm.generar("3") == "ya libre"
    assert llm.modelo == "principal"


def test_la_reescritura_puede_ir_con_otro_modelo(corpus) -> None:
    motor, tablas = corpus
    rapido = LLMFalso(reescritura="graph greedy routing")
    principal = LLMFalso(reescritura=AssertionError("no debia reescribir"))

    r = responder(motor, principal, "¿grafos?", llm_reescritura=rapido, tablas=tablas)

    assert r.consulta == "graph greedy routing"
    assert len(rapido.llamadas) == len(principal.llamadas) == 1


def test_un_tiempo_agotado_tambien_pasa_al_respaldo() -> None:
    import httpx

    cliente = _ClienteGemini({"principal": httpx.ReadTimeout("lento"), "respaldo": "ok"})
    llm = GeminiLLM("clave", "principal", ("respaldo",), cliente=cliente)

    assert llm.generar("x") == "ok"
    with pytest.raises(LLMError, match=r"Gemini no respondio \(ReadTimeout\)"):
        GeminiLLM("clave", "principal", cliente=cliente).generar("x")
