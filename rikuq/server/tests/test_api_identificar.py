"""Contrato HTTP completo con pipeline y motor falsos, sin modelos ni red."""

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.entrada import MAX_IMAGEN
from app.main import create_app
from app.pipeline import ModeloNoDisponible
from app.rostros.errores import EmptyObjectError, FeatureExtractionError
from app.servicio import Servicio
from tests.pipeline_falso import PipelineFalso


def _motor(similitud=0.9, *, vacio=False, fallo=None):
    def handler(request):
        if fallo:
            raise fallo
        assert request.url.path == "/query"  # nunca sube la foto al motor
        sql = json.loads(request.content)["sql"]
        if "SIMILAR_TO" in sql:
            assert "USING HNSW WITH METRIC=cosine" in sql
            assert sql.count("0.0000000") == 511
            return httpx.Response(
                200,
                json={
                    "columns": ["id", "persona_id", "ruta", "puntaje"],
                    "rows": [] if vacio else [[1, 7, "equipo/Ana/uno.jpg", similitud]],
                },
            )
        return httpx.Response(200, json={"columns": ["id", "nombre"], "rows": [[7, "Ana"]]})

    return httpx.MockTransport(handler)


def _cliente(pipeline=None, **kwargs):
    return TestClient(
        create_app(
            pipeline=pipeline or PipelineFalso(),
            engine_transport=_motor(**kwargs),
        )
    )


def test_identifica_y_expone_los_dos_tiempos_y_la_caja() -> None:
    pipeline = PipelineFalso()
    with _cliente(pipeline) as client:
        respuesta = client.post(
            "/identificar", files={"imagen": ("foto.jpg", b"foto", "image/jpeg")}
        )
    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["persona"] == "Ana"
    assert cuerpo["distancia"] == pytest.approx(0.1)
    assert cuerpo["confianza"] == pytest.approx((0.55 - 0.1) / 0.55)
    assert cuerpo["foto"] == "/personas/7/foto"
    assert cuerpo["caja"] == [10, 20, 80, 90]
    assert (cuerpo["ancho"], cuerpo["alto"]) == (640, 480)
    assert 0 <= cuerpo["ms_motor"] <= cuerpo["ms_total"]
    assert cuerpo["rostros"][0]["persona"] == "Ana"
    assert respuesta.headers["cache-control"] == "no-store"
    assert pipeline.recibidos == [b"foto"]


def test_un_desconocido_no_expone_el_nombre_ni_la_foto_del_vecino() -> None:
    with _cliente(similitud=0.1) as client:
        respuesta = client.post(
            "/identificar", content=b"cuadro", headers={"Content-Type": "image/jpeg"}
        )
    cuerpo = respuesta.json()
    assert cuerpo["persona"] is None
    assert cuerpo["foto"] is None
    assert cuerpo["persona_id"] is None
    assert cuerpo["confianza"] == 0
    assert cuerpo["distancia"] == pytest.approx(0.9)


def test_una_base_vacia_no_serializa_infinity() -> None:
    with _cliente(vacio=True) as client:
        respuesta = client.post(
            "/identificar", content=b"foto", headers={"Content-Type": "image/png"}
        )
    assert respuesta.status_code == 200
    assert respuesta.json()["distancia"] is None
    assert "Infinity" not in respuesta.text


def test_un_rostro_sin_persona_relacionada_no_inventa_confianza() -> None:
    def motor(request):
        sql = json.loads(request.content)["sql"]
        if "SIMILAR_TO" in sql:
            return httpx.Response(
                200,
                json={
                    "columns": ["id", "persona_id", "ruta", "puntaje"],
                    "rows": [[1, 7, "foto.jpg", 0.99]],
                },
            )
        return httpx.Response(200, json={"columns": ["id", "nombre"], "rows": []})

    with TestClient(
        create_app(pipeline=PipelineFalso(), engine_transport=httpx.MockTransport(motor))
    ) as client:
        response = client.post(
            "/identificar", content=b"foto", headers={"Content-Type": "image/jpeg"}
        )
    assert response.json()["persona"] is None
    assert response.json()["confianza"] == 0


def test_soporta_varios_rostros_y_suma_el_tiempo_de_busqueda() -> None:
    with _cliente(PipelineFalso(rostros=2)) as client:
        cuerpo = client.post(
            "/identificar", content=b"foto", headers={"Content-Type": "image/jpeg"}
        ).json()
    assert len(cuerpo["rostros"]) == 2
    assert cuerpo["ms_motor"] == pytest.approx(sum(r["ms_motor"] for r in cuerpo["rostros"]))


@pytest.mark.parametrize(
    ("error", "status", "codigo"),
    [
        (EmptyObjectError("no hay rostro"), 422, "sin_rostro"),
        (FeatureExtractionError("imagen corrupta"), 422, "imagen_invalida"),
        (ModeloNoDisponible("faltan modelos"), 503, "modelo_no_disponible"),
    ],
)
def test_errores_del_pipeline_tienen_estado_y_motivo(error, status, codigo) -> None:
    with _cliente(PipelineFalso(error)) as client:
        respuesta = client.post(
            "/identificar", content=b"foto", headers={"Content-Type": "image/jpeg"}
        )
    assert respuesta.status_code == status
    assert respuesta.json()["codigo"] == codigo
    assert respuesta.json()["error"]


def test_motor_apagado_no_es_un_error_de_la_imagen() -> None:
    with _cliente(fallo=httpx.ConnectError("apagado")) as client:
        respuesta = client.post(
            "/identificar", content=b"foto", headers={"Content-Type": "image/jpeg"}
        )
    assert respuesta.status_code == 503
    assert respuesta.json()["codigo"] == "motor_no_disponible"


def test_un_error_sql_no_filtra_vectores_al_navegador() -> None:
    def handler(_request):
        return httpx.Response(400, json={"detail": "VECTOR(datos privados)"})

    with TestClient(
        create_app(pipeline=PipelineFalso(), engine_transport=httpx.MockTransport(handler))
    ) as client:
        respuesta = client.post(
            "/identificar", content=b"foto", headers={"Content-Type": "image/jpeg"}
        )
    assert respuesta.status_code == 502
    assert "VECTOR" not in respuesta.text


@pytest.mark.parametrize(
    "files",
    [
        {"otro": ("x.jpg", b"x", "image/jpeg")},
        {"imagen": ("x.jpg", b"", "image/jpeg")},
    ],
)
def test_falta_una_imagen_valida_en_el_campo_correcto(files) -> None:
    with _cliente() as client:
        assert client.post("/identificar", files=files).status_code == 422


def test_no_admite_dos_archivos() -> None:
    with _cliente() as client:
        response = client.post(
            "/identificar",
            files=[
                ("imagen", ("a.jpg", b"a", "image/jpeg")),
                ("imagen", ("b.jpg", b"b", "image/jpeg")),
            ],
        )
    assert response.status_code == 422


def test_multipart_no_utiliza_temporales_de_starlette(monkeypatch) -> None:
    def prohibido(*args, **kwargs):
        raise AssertionError("una foto de consulta no debe volcarse a disco")

    monkeypatch.setattr("starlette.formparsers.SpooledTemporaryFile", prohibido)
    with _cliente() as client:
        assert (
            client.post(
                "/identificar", files={"imagen": ("foto.jpg", b"foto", "image/jpeg")}
            ).status_code
            == 200
        )


def test_el_pipeline_no_se_ejecuta_en_paralelo_y_libera_el_lock_tras_error() -> None:
    dentro, terminar = Event(), Event()

    class Bloqueante:
        def extraer(self, _datos):
            dentro.set()
            assert terminar.wait(5)
            raise EmptyObjectError("sin rostro")

    servicio = Servicio(Settings(), Bloqueante())
    with ThreadPoolExecutor(max_workers=1) as pool:
        futura = pool.submit(servicio.reconocer, b"uno")
        assert dentro.wait(5)
        try:
            from fastapi import HTTPException

            with pytest.raises(HTTPException) as error:
                servicio.reconocer(b"dos")
            assert error.value.status_code == 429
        finally:
            terminar.set()
        with pytest.raises(EmptyObjectError):
            futura.result(timeout=5)
    with pytest.raises(EmptyObjectError):
        servicio.reconocer(b"tres")


def test_rechaza_el_tipo_antes_de_ejecutar_el_pipeline() -> None:
    pipeline = PipelineFalso()
    with _cliente(pipeline) as client:
        assert (
            client.post(
                "/identificar", files={"imagen": ("x.svg", b"svg", "image/svg+xml")}
            ).status_code
            == 415
        )
    assert not pipeline.recibidos


def test_limita_el_tamano_incluso_sin_content_length() -> None:
    pipeline = PipelineFalso()
    with _cliente(pipeline) as client:
        respuesta = client.post(
            "/identificar",
            content=iter([b"x" * MAX_IMAGEN, b"x"]),
            headers={"Content-Type": "image/jpeg"},
        )
    assert respuesta.status_code == 413
    assert not pipeline.recibidos


def test_multipart_truncado_no_llega_al_pipeline() -> None:
    with _cliente() as client:
        response = client.post(
            "/identificar",
            content=b"--corte\r\n",
            headers={
                "Content-Type": "multipart/form-data; boundary=corte",
            },
        )
    assert response.status_code in (400, 422)


def test_el_servidor_importa_sin_opencv_numpy_ni_motor() -> None:
    programa = """
import sys
for nombre in ('cv2', 'numpy', 'engine', 'quipudb_native'):
    sys.modules[nombre] = None
from app.main import create_app
assert create_app().title == 'Rikuq'
"""
    subprocess.run(
        [sys.executable, "-c", programa],
        check=True,
        cwd=Path(__file__).resolve().parents[1],
        timeout=15,
    )


def test_solo_se_sirven_fotos_dentro_del_dataset(tmp_path) -> None:
    foto = tmp_path / "equipo" / "ana.jpg"
    foto.parent.mkdir()
    foto.write_bytes(b"foto guardada")

    def motor(_request):
        return httpx.Response(200, json={"columns": ["ruta"], "rows": [["equipo/ana.jpg"]]})

    with TestClient(
        create_app(datos=tmp_path, engine_transport=httpx.MockTransport(motor))
    ) as client:
        response = client.get("/personas/7/foto")
    assert response.status_code == 200
    assert response.content == b"foto guardada"
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("ruta", ["../fuera.jpg", "/etc/passwd", "equipo/imagen.svg"])
def test_no_expone_rutas_fuera_del_dataset(tmp_path, ruta) -> None:
    def motor(_request):
        return httpx.Response(200, json={"columns": ["ruta"], "rows": [[ruta]]})

    with TestClient(
        create_app(datos=tmp_path, engine_transport=httpx.MockTransport(motor))
    ) as client:
        assert client.get("/personas/7/foto").status_code == 404
