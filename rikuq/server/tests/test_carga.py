"""Carga del dataset en QuipuDB (#302), sin motor y sin modelos.

El motor se atiende con `httpx.MockTransport` y el pipeline de rostros se
reemplaza por uno falso que devuelve vectores inventados. Asi las pruebas
comprueban el SQL que se manda y que una imagen sin rostro no corta la carga,
sin levantar QuipuDB ni cargar 37 MB de ONNX.
"""

from pathlib import Path

import httpx
import numpy as np
import pytest

from app.carga.dataset import (
    cargar,
    crear_esquema,
    esquema_existe,
    insertar_persona,
    insertar_rostro,
    por_persona,
)
from app.carga.esquema import PERSONAS, ROSTROS, sentencias
from app.config import Settings
from app.dataset.lfw import Rostro
from app.motor import ClienteMotor
from app.rostros.errores import EmptyObjectError


def _motor(recibidas: list[str], tablas: list[str] | None = None) -> ClienteMotor:
    def handler(peticion: httpx.Request) -> httpx.Response:
        if peticion.url.path == "/tables":
            return httpx.Response(200, json=[{"name": t} for t in (tablas or [])])
        import json

        recibidas.append(json.loads(peticion.read())["sql"])
        return httpx.Response(200, json={"affected_rows": 1})

    settings = Settings(quipudb_api_url="http://127.0.0.1:8000")
    return ClienteMotor.desde(settings, transport=httpx.MockTransport(handler))


def _rostro(persona: str, numero: int, origen: str = "lfw") -> Rostro:
    return Rostro(persona, f"{origen}/{persona}/{persona}_{numero:04d}.jpg", "abc", origen)


class _PipelineFalso:
    """Devuelve un vector inventado, o se queja de las rutas que se le digan."""

    def __init__(self, sin_rostro: set[str] | None = None) -> None:
        self.sin_rostro = sin_rostro or set()
        self.vistas: list[str] = []

    def __call__(self, ruta, _detector, _descriptor):
        nombre = Path(ruta).name
        self.vistas.append(nombre)
        if nombre in self.sin_rostro:
            raise EmptyObjectError(f"no hay ningun rostro en {nombre}")
        valor = (len(self.vistas) % 10) / 10
        return (np.full(128, valor, dtype=np.float32),)


@pytest.fixture
def pipeline(monkeypatch: pytest.MonkeyPatch):
    def instalar(sin_rostro: set[str] | None = None) -> _PipelineFalso:
        falso = _PipelineFalso(sin_rostro)
        monkeypatch.setattr("app.carga.dataset.vectores_de", falso)
        return falso

    return instalar


# --- las sentencias -----------------------------------------------------------


def test_insertar_persona_escapa_el_nombre() -> None:
    sql = insertar_persona(1, "O'Brien", "equipo", 3)

    assert "'O''Brien'" in sql
    assert sql.startswith(f"INSERT INTO {PERSONAS}")


def test_insertar_rostro_escribe_el_vector_sin_notacion_cientifica() -> None:
    sql = insertar_rostro(7, 1, "lfw/Ana/a.jpg", [0.00003, 0.5])

    assert "VECTOR(0.0000300, 0.5000000)" in sql
    assert sql.startswith(f"INSERT INTO {ROSTROS}")


def test_por_persona_agrupa_conservando_el_orden() -> None:
    rostros = [_rostro("Ana", 1), _rostro("Beto", 1), _rostro("Ana", 2)]

    agrupado = por_persona(rostros)

    assert list(agrupado) == ["Ana", "Beto"]
    assert len(agrupado["Ana"]) == 2


# --- el esquema contra el motor -----------------------------------------------


def test_crear_esquema_manda_las_tablas_y_los_indices() -> None:
    recibidas: list[str] = []

    with _motor(recibidas) as cliente:
        crear_esquema(cliente)

    assert recibidas == list(sentencias())


def test_esquema_existe_mira_las_tablas_del_motor() -> None:
    with _motor([], tablas=[PERSONAS, ROSTROS, "otra"]) as cliente:
        assert esquema_existe(cliente) is True
    with _motor([], tablas=[PERSONAS]) as cliente:
        assert esquema_existe(cliente) is False


# --- la carga -----------------------------------------------------------------


def test_cargar_inserta_la_persona_y_sus_rostros(pipeline) -> None:
    pipeline()
    recibidas: list[str] = []

    with _motor(recibidas) as cliente:
        informe = cargar(cliente, [_rostro("Ana", 1), _rostro("Ana", 2)], detector=1, descriptor=1)

    enviado = "\n".join(recibidas)
    assert informe.personas == 1
    assert informe.rostros == 2
    assert enviado.count(f"INSERT INTO {PERSONAS}") == 1
    assert enviado.count(f"INSERT INTO {ROSTROS}") == 2


def test_cada_rostro_apunta_a_su_persona(pipeline) -> None:
    pipeline()
    recibidas: list[str] = []

    with _motor(recibidas) as cliente:
        cargar(cliente, [_rostro("Ana", 1), _rostro("Beto", 1)], detector=1, descriptor=1)

    enviado = "\n".join(recibidas)
    assert "VALUES (1, 'Ana'" in enviado
    assert "VALUES (2, 'Beto'" in enviado
    # El primer rostro es de la persona 1 y el segundo de la 2.
    assert "VALUES (1, 1, 'lfw/Ana/Ana_0001.jpg'" in enviado
    assert "VALUES (2, 2, 'lfw/Beto/Beto_0001.jpg'" in enviado


def test_la_persona_dice_cuantas_fotos_suyas_entraron(pipeline) -> None:
    pipeline()
    recibidas: list[str] = []

    with _motor(recibidas) as cliente:
        cargar(cliente, [_rostro("Ana", 1), _rostro("Ana", 2)], detector=1, descriptor=1)

    assert "'lfw', 2)" in "\n".join(recibidas)
