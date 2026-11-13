"""De una carpeta a SIMILAR_TO solo por REST, con el motor real (#287)."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from engine.api.main import create_app
from engine.api.test_vocabularies import images, train

pytest.importorskip("quipudb_native", reason="se necesitan los bindings compilados")
pytest.importorskip("cv2", reason="se necesita opencv-python-headless")


def wait_load(client, job_id):
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        body = client.get(f"/tables/fotos/load-media/{job_id}").json()
        if body["status"] in {"completed", "failed", "cancelled"}:
            return body
        time.sleep(0.01)
    pytest.fail("la carga no terminó")


def test_entrenar_cargar_y_buscar_sin_reiniciar_y_tras_reiniciar(tmp_path):
    root = tmp_path / "multimedia"
    images(root / "fotos")
    catalog = tmp_path / "catalogo.txt"
    similar = (
        "SELECT nombre FROM fotos WHERE SIMILAR_TO('fotos/foto_0.png', k=3) "
        "USING HNSW WITH METRIC=cosine"
    )

    with TestClient(create_app(catalog=catalog, media_root=root)) as client:
        created = client.post(
            "/query",
            json={
                "sql": "CREATE TABLE fotos (id INT PRIMARY KEY, nombre VARCHAR(64), "
                "ruta VARCHAR(512), rasgos VECTOR(8)) USING HEAP; "
                "CREATE INDEX por_grafo ON fotos (rasgos) USING HNSW WITH METRIC=cosine"
            },
        )
        assert created.status_code == 200, created.json()
        job = train(client, folder="fotos", k=8, table="fotos")
        assert job["status"] == "completed", job
        load = client.post(
            "/tables/fotos/load-media",
            json={"folder": "fotos", "vocabulary": job["vocabulary"]},
        )
        assert load.status_code == 202, load.json()
        report = wait_load(client, load.json()["job_id"])
        assert report["inserted"] == 6
        found = client.post("/query", json={"sql": similar})
        assert found.status_code == 200, found.json()
        before = found.json()["rows"]

    assert before[0] == ["foto_0.png"]
    with TestClient(create_app(catalog=catalog, media_root=root)) as client:
        again = client.post("/query", json={"sql": similar})
    assert again.status_code == 200, again.json()
    assert again.json()["rows"] == before
