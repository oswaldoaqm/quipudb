"""Servidor sin motor, modelos ni dataset."""

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def test_health_no_depende_del_motor() -> None:
    with TestClient(create_app(Settings(quipudb_api_url="http://no-existe.invalid"))) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "app": "rikuq"}


def test_cors_solo_permite_la_interfaz_configurada() -> None:
    with TestClient(create_app()) as client:
        allowed = client.get("/health", headers={"Origin": "http://localhost:5175"})
        denied = client.get("/health", headers={"Origin": "https://otro.invalid"})
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:5175"
    assert "access-control-allow-origin" not in denied.headers
