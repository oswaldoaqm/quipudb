"""El contrato HTTP vectorial mantiene catalogo, filas y errores ubicados."""

import json

from fastapi.testclient import TestClient

from engine.api.main import create_app
from engine.executor.test_similar_native import SQL
from engine.executor.test_similar_native import dataset as vector_dataset
from engine.executor.test_similar_native import q as native

dataset = vector_dataset  # Registrar la misma fixture en este modulo de pruebas HTTP.


def walk(step):
    for child in step["children"]:
        yield from walk(child)
    yield step


def test_http_crea_indices_busca_y_describe_catalogo(dataset, tmp_path):
    database, processor, _calls = dataset
    app = create_app(processor=processor, database=database, native=native, media_root=tmp_path)
    with TestClient(app) as client:
        for kind in ("HNSW", "IVF"):
            response = client.post(
                "/query",
                json={
                    "sql": f"CREATE INDEX por_{kind.lower()} ON fotos (rasgos) USING {kind}",
                },
            )
            assert response.status_code == 200, response.text
            response = client.post("/query", json={"sql": SQL.format(kind=kind, metric="cosine")})
            assert response.status_code == 200, response.text
            result = response.json()
            assert len(result["rows"]) > 0
            step = next(step for step in walk(result["plan"]["root"]) if step["op"] == "knn_search")
            assert (step["structure"], step["metric"]) == (kind.lower(), "cosine")
        response = client.get("/tables")
        assert response.status_code == 200, response.text
        assert {index["structure"] for index in response.json()[0]["indexes"]} == {"ivf", "hnsw"}
        assert all(not index["supports_range"] for index in response.json()[0]["indexes"])


def test_http_archivo_ausente_devuelve_400_con_linea_y_columna(dataset, tmp_path):
    database, processor, _calls = dataset
    app = create_app(processor=processor, database=database, native=native, media_root=tmp_path)
    sql = "\n" + SQL.format(kind="HNSW", metric="cosine").replace("consulta.png", "ausente.png")
    with TestClient(app) as client:
        response = client.post("/query", json={"sql": sql})
        assert response.status_code == 400
        assert response.json()["line"] == 2
        assert response.json()["column"] == sql.splitlines()[1].index("'ausente.png'") + 1
        assert response.json()["kind"] == "semantic"


def test_motor_lazy_recibe_vocabularios_del_entorno_y_raiz_de_app(dataset, tmp_path, monkeypatch):
    database, _processor, _calls = dataset
    database.flush()
    monkeypatch.setenv("QUIPUDB_MEDIA_VOCABULARIES", json.dumps({"fotos": "vocabulario.npz"}))
    app = create_app(catalog=tmp_path / "catalogo.txt", media_root=tmp_path)
    with TestClient(app) as client:
        response = client.post("/query", json={"sql": SQL.format(kind="HNSW", metric="cosine")})
        assert response.status_code == 200, response.text
        assert len(response.json()["rows"]) == 3
