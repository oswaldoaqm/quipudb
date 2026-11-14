"""Subir una carpeta y una foto de consulta y buscar con SIMILAR_TO, con el motor real (#288)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from engine.api.main import create_app
from engine.api.test_uploads import upload
from engine.api.test_vocabularies import images, train
from engine.api.test_vocabularies_native import wait_load

pytest.importorskip("quipudb_native", reason="se necesitan los bindings compilados")
pytest.importorskip("cv2", reason="se necesita opencv-python-headless")


def test_de_archivos_del_navegador_a_similar_to(tmp_path):
    local = images(tmp_path / "en_mi_computador")  # fuera de la raíz multimedia
    root = tmp_path / "multimedia"
    with TestClient(create_app(catalog=tmp_path / "catalogo.txt", media_root=root)) as client:
        created = client.post(
            "/query",
            json={
                "sql": "CREATE TABLE fotos (id INT PRIMARY KEY, nombre VARCHAR(64), "
                "ruta VARCHAR(512), rasgos VECTOR(8)) USING HEAP; "
                "CREATE INDEX por_grafo ON fotos (rasgos) USING HNSW WITH METRIC=cosine"
            },
        )
        assert created.status_code == 200, created.json()
        files = [("files", (p.name, p.read_bytes(), "image/png")) for p in local]
        carpeta = client.post("/media/uploads", files=files, data={"name": "fotos"}).json()
        assert carpeta["saved"] == len(local)

        job = train(client, folder=carpeta["folder"], k=8, table="fotos")
        assert job["status"] == "completed", job
        load = client.post(
            "/tables/fotos/load-media",
            json={"folder": carpeta["folder"], "vocabulary": job["vocabulary"]},
        )
        assert wait_load(client, load.json()["job_id"])["inserted"] == len(local)

        # La foto de consulta llega aparte, como la elegiría el usuario.
        (consulta,) = upload(client, ["consulta.png"], content=local[2].read_bytes()).json()[
            "files"
        ]
        found = client.post(
            "/query",
            json={
                "sql": f"SELECT nombre FROM fotos WHERE SIMILAR_TO('{consulta['path']}', k=1) "
                "USING HNSW WITH METRIC=cosine"
            },
        )
    assert found.status_code == 200, found.json()
    assert found.json()["rows"] == [[local[2].name]]
