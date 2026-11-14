"""Carga del dataset en QuipuDB (#302), sin motor y sin modelos.

El motor se atiende con `httpx.MockTransport` y el pipeline de rostros se
reemplaza por uno falso que devuelve vectores inventados. Asi las pruebas
comprueban el SQL que se manda y que una imagen sin rostro no corta la carga,
sin levantar QuipuDB ni cargar 37 MB de ONNX.
"""

import httpx

from app.carga.dataset import (
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
