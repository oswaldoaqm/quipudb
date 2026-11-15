"""Identificar a quien pertenece un rostro (#303), sin motor.

El motor se atiende con `httpx.MockTransport`: las pruebas comprueban el SQL
que sale, como se lee el puntaje y, sobre todo, que un rostro que no esta
cargado salga **desconocido** y no el vecino mas cercano.
"""

import json

import httpx
import numpy as np
import pytest

from app.carga.esquema import PERSONAS, ROSTROS
from app.config import Settings
from app.consulta.identificar import VECINOS, consulta_sql, distancia_de, nombres_de, vecinos
from app.motor import ClienteMotor
from app.rostros.embedding import DIMENSIONES


def _vector(valor: float = 0.1) -> np.ndarray:
    crudo = np.full(DIMENSIONES, valor, dtype=np.float32)
    return crudo / np.linalg.norm(crudo)


def _motor(rostros: list[dict], personas: dict[int, str], vistas: list[str] | None = None):
    """Un motor que responde esos vecinos y esos nombres."""

    def handler(peticion: httpx.Request) -> httpx.Response:
        sql = json.loads(peticion.read())["sql"]
        if vistas is not None:
            vistas.append(sql)
        if sql.startswith(f"SELECT id, nombre FROM {PERSONAS}"):
            pedido = int(sql.rsplit("=", 1)[1])
            filas = [[pedido, personas[pedido]]] if pedido in personas else []
            return httpx.Response(200, json={"columns": ["id", "nombre"], "rows": filas})
        return httpx.Response(
            200,
            json={
                "columns": ["id", "persona_id", "ruta", "puntaje"],
                "rows": [[r["id"], r["persona_id"], r["ruta"], r["puntaje"]] for r in rostros],
            },
        )

    settings = Settings(quipudb_api_url="http://127.0.0.1:8000")
    return ClienteMotor.desde(settings, transport=httpx.MockTransport(handler))


def _fila(rostro_id: int, persona_id: int, puntaje: float, ruta: str = "lfw/x/x.jpg") -> dict:
    return {"id": rostro_id, "persona_id": persona_id, "ruta": ruta, "puntaje": puntaje}


# --- la consulta --------------------------------------------------------------


def test_la_consulta_manda_un_vector_y_no_un_archivo() -> None:
    """El vector lo produce ArcFace, no el vocabulario visual de la Parte 4."""

    sql = consulta_sql(_vector(), k=3)

    assert "SIMILAR_TO(embedding, VECTOR(" in sql
    assert ".jpg" not in sql


def test_la_consulta_usa_el_indice_hnsw_con_coseno() -> None:
    sql = consulta_sql(_vector())

    assert "USING HNSW WITH METRIC=cosine" in sql
    assert f"FROM {ROSTROS}" in sql


def test_la_consulta_pide_el_puntaje_de_similitud() -> None:
    assert "SIMILARITY_SCORE() AS puntaje" in consulta_sql(_vector())


def test_la_consulta_respeta_el_k_que_se_le_pide() -> None:
    assert "k=3)" in consulta_sql(_vector(), k=3)
    assert f"k={VECINOS})" in consulta_sql(_vector())


def test_el_puntaje_del_motor_se_vuelve_distancia() -> None:
    """Con coseno el motor devuelve una similitud: mayor es mas parecido."""

    assert distancia_de(1.0) == pytest.approx(0.0)
    assert distancia_de(0.0) == pytest.approx(1.0)
    assert distancia_de(-1.0) == pytest.approx(2.0)


def test_los_vecinos_salen_del_mas_parecido_al_menos() -> None:
    with _motor([_fila(1, 1, 0.2), _fila(2, 2, 0.9), _fila(3, 3, 0.5)], {}) as cliente:
        encontrados = vecinos(cliente, _vector())

    assert [c.rostro_id for c in encontrados] == [2, 3, 1]
    assert encontrados[0].distancia == pytest.approx(0.1)


def test_el_nombre_se_trae_aparte_porque_similar_to_no_admite_join() -> None:
    vistas: list[str] = []

    with _motor([], {1: "Ana", 2: "Beto"}, vistas) as cliente:
        assert nombres_de(cliente, [1, 2, 1]) == {1: "Ana", 2: "Beto"}

    # Una consulta por id distinto, sin repetir el 1.
    assert len(vistas) == 2
