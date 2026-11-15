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
from app.consulta.identificar import (
    UMBRAL_POR_DEFECTO,
    VECINOS,
    Candidato,
    Identificacion,
    confianza_de,
    consulta_sql,
    distancia_de,
    identificar,
    nombres_de,
    vecinos,
)
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


# --- el umbral ----------------------------------------------------------------


def test_un_rostro_cargado_se_identifica() -> None:
    with _motor([_fila(1, 7, 0.95)], {7: "Ana_Lopez"}) as cliente:
        respuesta = identificar(cliente, _vector(), umbral=0.5)

    assert respuesta.conocido
    assert respuesta.persona == "Ana_Lopez"
    assert respuesta.distancia == pytest.approx(0.05)


def test_un_rostro_ajeno_sale_desconocido() -> None:
    """Es el criterio del issue: el vecino mas cercano siempre existe."""

    with _motor([_fila(1, 7, 0.1)], {7: "Ana_Lopez"}) as cliente:
        respuesta = identificar(cliente, _vector(), umbral=0.5)

    assert not respuesta.conocido
    assert respuesta.persona is None
    assert respuesta.distancia == pytest.approx(0.9)


def test_el_desconocido_igual_trae_los_candidatos() -> None:
    """Sirve para depurar por que no reconocio, sin volver a consultar."""

    with _motor([_fila(1, 7, 0.1)], {7: "Ana_Lopez"}) as cliente:
        respuesta = identificar(cliente, _vector(), umbral=0.5)

    assert respuesta.candidatos[0].persona == "Ana_Lopez"
    assert respuesta.candidatos[0].distancia == pytest.approx(0.9)


def test_justo_en_el_umbral_se_acepta() -> None:
    """El criterio es 'a lo sumo esta lejos', no 'estrictamente menor'."""

    with _motor([_fila(1, 7, 0.5)], {7: "Ana"}) as cliente:
        assert identificar(cliente, _vector(), umbral=0.5).conocido


def test_una_base_vacia_no_revienta() -> None:
    with _motor([], {}) as cliente:
        respuesta = identificar(cliente, _vector())

    assert not respuesta.conocido
    assert respuesta.confianza == 0.0


def test_el_umbral_por_defecto_cae_en_el_hueco_medido() -> None:
    """Con ArcFace: misma persona 0.19-0.37, personas distintas 0.90-1.06."""

    assert 0.37 < UMBRAL_POR_DEFECTO < 0.90


# --- la confianza -------------------------------------------------------------


def test_la_confianza_es_1_cuando_el_vector_es_identico() -> None:
    assert confianza_de(0.0, 0.5) == pytest.approx(1.0)


def test_la_confianza_es_0_justo_en_el_umbral() -> None:
    assert confianza_de(0.5, 0.5) == pytest.approx(0.0)


def test_la_confianza_no_se_sale_de_cero_y_uno() -> None:
    assert confianza_de(10.0, 0.5) == 0.0
    assert confianza_de(-1.0, 0.5) == 1.0


def test_la_respuesta_trae_confianza_y_no_solo_el_nombre() -> None:
    """Una identificacion al 5% hay que mirarla aunque haya pasado."""

    with _motor([_fila(1, 7, 0.6)], {7: "Ana"}) as cliente:
        respuesta = identificar(cliente, _vector(), umbral=0.5)

    assert respuesta.conocido
    assert 0.0 < respuesta.confianza < 0.3


def test_el_desconocido_no_reporta_confianza() -> None:
    with _motor([_fila(1, 7, 0.0)], {7: "Ana"}) as cliente:
        assert identificar(cliente, _vector(), umbral=0.5).confianza == 0.0


def test_la_identificacion_se_lee_de_un_vistazo() -> None:
    conocida = Identificacion("Ana", 0.2, 0.6, (Candidato(1, 1, "a.jpg", 0.2, "Ana"),))
    ajena = Identificacion(None, 0.9, 0.0)

    assert "Ana" in str(conocida) and "60%" in str(conocida)
    assert "margen al umbral" in str(conocida)
    assert "confianza" not in str(conocida)
    assert "desconocido" in str(ajena)


def test_el_umbral_por_defecto_es_el_que_midio_el_barrido() -> None:
    """0.55 con ArcFace: 92.9% de precision y 0 falsos positivos de 309 ajenas."""

    assert UMBRAL_POR_DEFECTO == 0.55
