"""``POST /ask`` (#247): el RAG completo detras de un endpoint."""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.motor import ClienteMotor
from app.rag.generacion import SIN_INFORMACION
from app.rag.llm import LLMError
from tests.llm_falso import LLMFalso


def _ops(paso: dict) -> list[str]:
    return [paso["op"]] + [op for hijo in paso["children"] for op in _ops(hijo)]


def _motor_caido() -> ClienteMotor:
    def handler(peticion: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("conexion rechazada", request=peticion)

    return ClienteMotor("http://127.0.0.1:1", transport=httpx.MockTransport(handler))


def _cliente(**opciones) -> TestClient:
    opciones.setdefault("motor", _motor_caido())
    return TestClient(create_app(Settings(gemini_api_key="clave"), **opciones))


# ---------------------------------------------------------------------------
# Sin motor
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "cuerpo",
    [{"pregunta": ""}, {"pregunta": "x", "k": 0}, {"pregunta": "x", "ranking": "COSENO"}, {}],
)
def test_una_peticion_mal_formada_es_422(cuerpo) -> None:
    assert _cliente(llm=LLMFalso()).post("/ask", json=cuerpo).status_code == 422


def test_con_el_motor_caido_responde_503_y_dice_que_fue_el_motor() -> None:
    respuesta = _cliente(llm=LLMFalso()).post("/ask", json={"pregunta": "HNSW"})

    assert respuesta.status_code == 503
    assert respuesta.json()["origen"] == "motor"
    assert "no se pudo contactar al motor" in respuesta.json()["error"]


def test_sin_clave_de_gemini_responde_503_y_dice_que_fue_el_llm() -> None:
    app = create_app(Settings(), motor=_motor_caido())

    respuesta = TestClient(app).post("/ask", json={"pregunta": "HNSW"})

    assert respuesta.status_code == 503
    assert respuesta.json() == {
        "error": "falta GEMINI_API_KEY en quipucamayoc/.env",
        "origen": "llm",
    }


def test_la_interfaz_puede_llamar_a_ask() -> None:
    preflight = _cliente(llm=LLMFalso()).options(
        "/ask",
        headers={
            "Origin": "http://localhost:5174",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )

    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == "http://localhost:5174"


# ---------------------------------------------------------------------------
# Con el motor real y el LLM falso
# ---------------------------------------------------------------------------


def test_ask_devuelve_respuesta_fuentes_sql_y_tiempos(corpus) -> None:
    motor, tablas = corpus
    llm = LLMFalso(
        reescritura="graph greedy routing",
        respuesta="Baja por capas [1]; los indices invertidos usan centroides [9].",
    )
    cliente = _cliente(motor=motor, llm=llm, tablas=tablas)

    respuesta = cliente.post("/ask", json={"pregunta": "¿Cómo busca un grafo?", "k": 4})

    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["consulta"] == "graph greedy routing"
    assert cuerpo["respuesta"] == "Baja por capas [1]; los indices invertidos usan centroides."
    assert (cuerpo["citas"], cuerpo["citas_descartadas"]) == ([1], [9])
    assert (cuerpo["modelo"], cuerpo["ranking"]) == ("falso-1", "BM25")

    fuentes = cuerpo["fuentes"]
    assert [f["numero"] for f in fuentes] == list(range(1, len(fuentes) + 1))
    assert 1 <= len(fuentes) <= 4
    assert fuentes[0]["citada"] is True
    assert fuentes[0]["paper"]["arxiv_id"] == "2401.00001v1"
    assert "greedy routing" in fuentes[0]["texto"].lower()

    match, *papers = cuerpo["sql"]
    assert "MATCH(texto, 'graph greedy routing') USING BM25" in match["sql"]
    assert "text_search" in _ops(match["plan"]["root"])
    assert all("WHERE id = " in s["sql"] for s in papers)
    tiempos = cuerpo["tiempos"]
    assert tiempos["total_ms"] >= tiempos["recuperacion_ms"] > 0


def test_ask_con_tf_idf_y_sin_reescribir(corpus) -> None:
    motor, tablas = corpus
    llm = LLMFalso(reescritura=AssertionError("no debia reescribir"))

    cuerpo = (
        _cliente(motor=motor, llm=llm, tablas=tablas)
        .post("/ask", json={"pregunta": "histograms selectivity", "ranking": "TF_IDF", "reescribir": False})
        .json()
    )

    assert (cuerpo["consulta"], cuerpo["ranking"]) == ("histograms selectivity", "TF_IDF")
    assert "USING TF_IDF" in cuerpo["sql"][0]["sql"]


def test_ask_sin_evidencias_no_llama_al_llm(corpus) -> None:
    motor, tablas = corpus
    llm = LLMFalso(reescritura="helicopter", respuesta=AssertionError("no debia responder"))

    cuerpo = _cliente(motor=motor, llm=llm, tablas=tablas).post("/ask", json={"pregunta": "submarino"}).json()

    assert (cuerpo["respuesta"], cuerpo["modelo"], cuerpo["fuentes"]) == (SIN_INFORMACION, None, [])


def test_un_error_de_gemini_al_responder_es_502_del_llm(corpus) -> None:
    motor, tablas = corpus
    llm = LLMFalso(respuesta=LLMError("se alcanzo el limite de uso de Gemini"))

    respuesta = _cliente(motor=motor, llm=llm, tablas=tablas).post("/ask", json={"pregunta": "graph"})

    assert respuesta.status_code == 502
    assert respuesta.json() == {"error": "se alcanzo el limite de uso de Gemini", "origen": "llm"}


# ---------------------------------------------------------------------------
# /ask/stream: una linea JSON por etapa
# ---------------------------------------------------------------------------


def _eventos(respuesta) -> list[dict]:
    import json

    assert respuesta.headers["content-type"].startswith("application/x-ndjson")
    return [json.loads(linea) for linea in respuesta.text.splitlines() if linea]


def test_el_stream_avisa_consulta_recuperacion_y_respuesta(corpus) -> None:
    motor, tablas = corpus
    llm = LLMFalso(reescritura="graph greedy routing", respuesta="Baja por capas [1].")

    respuesta = _cliente(motor=motor, llm=llm, tablas=tablas).post(
        "/ask/stream", json={"pregunta": "¿grafos?"}
    )

    eventos = _eventos(respuesta)
    assert [e["etapa"] for e in eventos] == ["consulta", "recuperacion", "respuesta"]
    assert eventos[0]["consulta"] == "graph greedy routing"
    assert eventos[1]["fuentes"] and not any(f["citada"] for f in eventos[1]["fuentes"])
    assert "MATCH" in eventos[1]["sql"][0]["sql"]
    final = eventos[2]["respuesta"]
    assert (final["respuesta"], final["citas"]) == ("Baja por capas [1].", [1])


def test_el_stream_termina_con_un_error_si_gemini_falla(corpus) -> None:
    motor, tablas = corpus
    llm = LLMFalso(respuesta=LLMError("Gemini no esta disponible ahora (503); vuelve a intentar"))

    eventos = _eventos(
        _cliente(motor=motor, llm=llm, tablas=tablas).post("/ask/stream", json={"pregunta": "graph"})
    )

    assert [e["etapa"] for e in eventos] == ["consulta", "recuperacion", "error"]
    assert eventos[-1]["origen"] == "llm"


def test_el_stream_con_el_motor_caido_avisa_error_del_motor() -> None:
    eventos = _eventos(_cliente(llm=LLMFalso()).post("/ask/stream", json={"pregunta": "HNSW"}))

    assert [e["etapa"] for e in eventos] == ["consulta", "error"]
    assert eventos[-1]["origen"] == "motor"


def test_el_stream_sin_clave_responde_503_antes_de_empezar() -> None:
    respuesta = TestClient(create_app(Settings(), motor=_motor_caido())).post(
        "/ask/stream", json={"pregunta": "HNSW"}
    )

    assert respuesta.status_code == 503
    assert respuesta.json()["origen"] == "llm"
