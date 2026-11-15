"""Motor de integracion propiedad de la prueba: nunca el servicio del usuario."""

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from app.config import Settings
from app.motor import ClienteMotor, MotorError

REPO = Path(__file__).resolve().parents[3]


def entorno_aislado(carpeta: Path, heredado: dict[str, str]) -> dict[str, str]:
    entorno = {clave: valor for clave, valor in heredado.items() if not clave.startswith("QUIPUDB_")}
    entorno["QUIPUDB_CATALOG"] = str(carpeta / "catalogo.txt")
    entorno["QUIPUDB_MEDIA_ROOT"] = str(carpeta / "multimedia")
    # No depender del PYTHONPATH del terminal ni importar app desde otra aplicacion.
    entorno["PYTHONPATH"] = os.pathsep.join((str(REPO / "build-py" / "bindings"), str(REPO)))
    entorno["PYTHONDONTWRITEBYTECODE"] = "1"
    return entorno


@pytest.fixture(name="motor_aislado")
def motor_aislado(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    carpeta = tmp_path / "motor-propio"
    carpeta.mkdir()
    entorno = entorno_aislado(carpeta, dict(os.environ))
    comprobacion = subprocess.run(
        [sys.executable, "-c", "import quipudb_native"],
        cwd=REPO,
        env=entorno,
        capture_output=True,
        timeout=15,
    )
    if comprobacion.returncode:
        pytest.skip("faltan bindings compatibles para crear el motor temporal de integracion")

    with socket.socket() as reserva:
        reserva.bind(("127.0.0.1", 0))
        puerto = reserva.getsockname()[1]
    url = f"http://127.0.0.1:{puerto}"
    proceso = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "engine.api.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(puerto),
            "--log-level",
            "warning",
        ],
        cwd=REPO,
        env=entorno,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        settings = Settings(quipudb_api_url=url, quipudb_timeout_s=0.5)
        listo = False
        with ClienteMotor.desde(settings) as cliente:
            for _ in range(100):
                if proceso.poll() is not None:
                    salida = proceso.communicate(timeout=5)[0].decode(errors="replace")
                    pytest.fail(f"el motor temporal no arranco: {salida[-2000:]}")
                try:
                    # Antes de cualquier escritura debe ser un catalogo completamente vacio.
                    assert cliente.table_names() == set()
                    listo = True
                    break
                except MotorError:
                    time.sleep(0.05)
        if not listo:
            pytest.fail("el motor temporal no respondio; no se uso un servicio alternativo")
        monkeypatch.setenv("QUIPUDB_URL", url)
        monkeypatch.setenv("QUIPUDB_API_URL", url)
        monkeypatch.setenv("QUIPUDB_TIMEOUT_S", "5")
        monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
        yield url
    finally:
        if proceso.poll() is None:
            proceso.terminate()
        try:
            proceso.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            proceso.kill()
            proceso.communicate(timeout=5)
