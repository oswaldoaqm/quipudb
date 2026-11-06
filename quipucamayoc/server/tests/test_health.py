"""El esqueleto del servidor: configuracion y /health, sin red ni clave."""

from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from app.config import DEFAULT_MODEL, Settings, load_settings
from app.main import create_app


def _motor(handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


def _cliente(settings: Settings, transport: httpx.BaseTransport) -> TestClient:
    return TestClient(create_app(settings, engine_transport=transport))


def test_health_dice_que_el_motor_responde_y_cuantas_tablas_tiene() -> None:
    def motor(peticion: httpx.Request) -> httpx.Response:
        assert peticion.url.path == "/tables"
        return httpx.Response(200, json=[{"name": "papers"}, {"name": "chunks"}])

    cuerpo = _cliente(Settings(gemini_api_key="clave-secreta"), _motor(motor)).get("/health").json()

    assert cuerpo["status"] == "ok"
    assert cuerpo["engine"] == {
        "url": "http://localhost:8000",
        "reachable": True,
        "tables": 2,
        "error": None,
    }
    assert cuerpo["llm"] == {"provider": "gemini", "model": DEFAULT_MODEL, "configured": True}


def test_health_con_el_motor_caido_responde_igual_y_explica_por_que() -> None:
    def motor(peticion: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("conexion rechazada", request=peticion)

    respuesta = _cliente(Settings(), _motor(motor)).get("/health")

    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["status"] == "degraded"
    assert cuerpo["engine"]["reachable"] is False
    assert "conexion rechazada" in cuerpo["engine"]["error"]
    assert cuerpo["llm"]["configured"] is False


def test_un_motor_que_responde_error_no_cuenta_como_vivo() -> None:
    cuerpo = (
        _cliente(Settings(), _motor(lambda _: httpx.Response(500, text="boom")))
        .get("/health")
        .json()
    )

    assert cuerpo["engine"]["reachable"] is False


def test_la_clave_nunca_sale_en_la_respuesta_ni_en_el_repr() -> None:
    settings = Settings(gemini_api_key="clave-secreta")
    respuesta = _cliente(settings, _motor(lambda _: httpx.Response(200, json=[]))).get("/health")

    assert "clave-secreta" not in respuesta.text
    assert "clave-secreta" not in repr(settings)


def test_cors_solo_para_la_interfaz() -> None:
    cliente = _cliente(Settings(), _motor(lambda _: httpx.Response(200, json=[])))

    permitido = cliente.get("/health", headers={"Origin": "http://localhost:5174"})
    ajeno = cliente.get("/health", headers={"Origin": "http://evil.example"})

    assert permitido.headers["access-control-allow-origin"] == "http://localhost:5174"
    assert "access-control-allow-origin" not in ajeno.headers


def test_el_entorno_gana_sobre_el_archivo(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "QUIPUDB_API_URL=http://archivo:9000/\n"
        "GEMINI_API_KEY=del-archivo\n"
        "GEMINI_MODEL=modelo-del-archivo\n"
        "QUIPUCAMAYOC_CORS_ORIGINS=http://a:1, http://b:2\n",
        encoding="utf-8",
    )

    settings = load_settings(env, environ={"GEMINI_MODEL": "modelo-del-entorno"})

    assert settings.quipudb_api_url == "http://archivo:9000"
    assert settings.gemini_api_key == "del-archivo"
    assert settings.gemini_model == "modelo-del-entorno"
    assert settings.cors_origins == ("http://a:1", "http://b:2")


def test_sin_archivo_ni_entorno_quedan_los_valores_por_omision(tmp_path: Path) -> None:
    settings = load_settings(tmp_path / "no-existe.env", environ={"GEMINI_API_KEY": "  "})

    assert settings == Settings()
    assert settings.llm_configured is False
