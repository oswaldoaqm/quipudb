"""Carpetas pequenas de imagenes y audio por REST hasta las paginas nativas."""

from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from engine.api.main import create_app
from engine.api.test_media_load import wait
from engine.executor import QueryProcessor
from engine.executor.media_load import MediaRow
from engine.multimedia.features import build_vocabulary, describe, extract_features

quipudb = pytest.importorskip("quipudb_native", reason="se necesitan los bindings compilados")
pytest.importorskip("cv2", reason="se necesita opencv-python-headless")
pytest.importorskip("soundfile", reason="se necesita soundfile")

DATA = Path(__file__).parents[1] / "multimedia" / "datos"


def test_los_lotes_mantienen_tambien_indices_vectoriales_ivf_y_hnsw(tmp_path):
    database = quipudb.Database(tmp_path / "catalogo.txt")
    processor = QueryProcessor(database)
    processor.execute(
        "CREATE TABLE objetos (id INT PRIMARY KEY, nombre VARCHAR(64), "
        "ruta VARCHAR(512), rasgos VECTOR(4))"
    )
    metric = quipudb.VectorMetric.EUCLIDEAN
    hnsw = database.create_hnsw_index("objetos", "por_grafo", "rasgos", metric)
    ivf = database.create_ivf_index("objetos", "por_listas", "rasgos", [[0.0] * 4, [1.0] * 4])
    report = processor.load_media_batch(
        "objetos",
        [MediaRow(1, "uno.png", "/uno.png", (0.0,) * 4),
         MediaRow(2, "dos.png", "/dos.png", (1.0,) * 4)],
    )
    assert (report.inserted, report.failed) == (2, 0)
    assert hnsw.size() == ivf.size() == 2
    assert len(hnsw.k_nearest([0.0] * 4, 2, metric, 10)) == 2
    assert len(ivf.k_nearest([0.0] * 4, 2, metric, 2)) == 2


@pytest.mark.parametrize("extension", ["png", "wav"])
def test_carpeta_real_con_fallos_llega_a_vector_indices_y_catalogo(tmp_path, extension):
    folder = tmp_path / "objetos"
    folder.mkdir()
    picture = folder / f"ejemplo.{extension}"
    shutil.copyfile(DATA / picture.name, picture)
    (folder / f"roto.{extension}").write_bytes(b"archivo defectuoso")
    (folder / "notas.txt").write_text("no es multimedia", encoding="utf-8")
    descriptors = describe(picture)
    vocabulary = build_vocabulary([descriptors[::2], descriptors[1::2]], 4, seed=187)
    vocabulary.save(tmp_path / "vocabulario.npz")
    database = quipudb.Database(tmp_path / "catalogo.txt")
    processor = QueryProcessor(database)
    processor.execute(
        "CREATE TABLE objetos (id INT PRIMARY KEY, nombre VARCHAR(64), "
        "ruta VARCHAR(512), rasgos VECTOR(4)) USING HEAP"
    )
    processor.execute("CREATE INDEX por_nombre ON objetos (nombre) USING BPLUS")
    app = create_app(
        processor=processor, database=database, native=quipudb, media_root=tmp_path
    )
    with TestClient(app) as client:
        response = client.post(
            "/tables/objetos/load-media",
            json={"folder": "objetos", "vocabulary": "vocabulario.npz", "batch_size": 1},
        )
        assert response.status_code == 202
        report = wait(client, response.json()["job_id"])
        assert report["status"] == "completed", report
        assert (report["inserted"], report["failed"], report["processed"]) == (1, 2, 3)
        rows = client.post(
            "/query", json={"sql": f"SELECT * FROM objetos WHERE nombre = '{picture.name}'"}
        ).json()["rows"]
        assert len(rows) == 1
        assert rows[0][1:3] == [picture.name, str(picture)]
        assert rows[0][3] == pytest.approx(extract_features(picture, vocabulary).tolist())
        assert len(database.index("objetos", "por_nombre").search(picture.name)) == 1
        assert client.get("/tables").json()[0]["record_count"] == 1
    database.flush()
    reopened = quipudb.Database(tmp_path / "catalogo.txt")
    record = next(iter(reopened.table("objetos").scan()))
    assert record[1] == picture.name
    assert np.isfinite(record[3]).all()
    assert len(record[3]) == 4


def test_imagen_sin_descriptores_se_inserta_como_vector_nulo(tmp_path):
    import cv2

    folder = tmp_path / "objetos"
    folder.mkdir()
    success, encoded = cv2.imencode(".png", np.zeros((32, 32), dtype=np.uint8))
    assert success
    (folder / "lisa.png").write_bytes(encoded.tobytes())
    descriptors = describe(DATA / "ejemplo.png")
    build_vocabulary([descriptors[::2], descriptors[1::2]], 4, seed=187).save(
        tmp_path / "vocabulario.npz"
    )
    database = quipudb.Database(tmp_path / "catalogo.txt")
    processor = QueryProcessor(database)
    processor.execute(
        "CREATE TABLE objetos (id INT PRIMARY KEY, nombre VARCHAR(64), "
        "ruta VARCHAR(512), rasgos VECTOR(4))"
    )
    app = create_app(
        processor=processor, database=database, native=quipudb, media_root=tmp_path
    )
    with TestClient(app) as client:
        response = client.post(
            "/tables/objetos/load-media",
            json={"folder": "objetos", "vocabulary": "vocabulario.npz"},
        )
        report = wait(client, response.json()["job_id"])
        assert report["inserted"] == 1
        assert report["failed"] == 0
    assert next(iter(database.table("objetos").scan()))[3] == [0.0] * 4
