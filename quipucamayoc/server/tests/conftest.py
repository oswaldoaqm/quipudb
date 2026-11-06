"""El motor real para las pruebas de punta a punta.

Dos formas, en este orden:

1. ``QUIPUCAMAYOC_TEST_MOTOR_URL=http://127.0.0.1:8000``: un motor ya
   levantado. Las tablas de la prueba llevan un prefijo unico y se borran al
   final, asi que no tocan el corpus.
2. Si el modulo nativo se puede importar (``PYTHONPATH=build-py/bindings``),
   el API del motor corre dentro del proceso con ``TestClient`` y un catalogo
   temporal. Es lo que hace el CI en el job de los bindings.

Sin ninguna de las dos, las pruebas se saltan: la aplicacion no necesita el
core compilado para el resto de sus pruebas.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import uuid
from pathlib import Path

import pytest

from app.motor import ClienteMotor, MotorError

_RAIZ = Path(__file__).resolve().parents[3]


def _motor_en_proceso(catalogo: Path) -> ClienteMotor:
    if str(_RAIZ) not in sys.path:
        sys.path.insert(0, str(_RAIZ))
    from engine.api.main import create_app
    from fastapi.testclient import TestClient

    http = TestClient(create_app(catalog=catalogo))
    return ClienteMotor(str(http.base_url), http=http)


@pytest.fixture()
def motor_real(tmp_path: Path):
    """``(cliente, prefijo)``: el prefijo aisla las tablas de cada prueba."""

    url = os.environ.get("QUIPUCAMAYOC_TEST_MOTOR_URL")
    if url:
        cliente = ClienteMotor(url)
    elif importlib.util.find_spec("quipudb_native") is not None:
        cliente = _motor_en_proceso(tmp_path / "catalogo.txt")
    else:
        pytest.skip("sin QUIPUCAMAYOC_TEST_MOTOR_URL ni modulo nativo no hay motor real")

    prefijo = f"t{uuid.uuid4().hex[:8]}_"
    yield cliente, prefijo
    try:
        for tabla in sorted(cliente.table_names()):
            if tabla.startswith(prefijo):
                cliente.query(f"DROP TABLE {tabla}")
    except MotorError:
        pass
    cliente.close()
