"""Tablas single: conservar texto, vectores, espacial y tipos del motor real."""

import pytest
from fastapi.testclient import TestClient

from engine.api.service import to_response
from engine.distributed.catalog import DistributedCatalog
from engine.distributed.fragments import FragmentService
from engine.distributed.node_api import create_node_app
from engine.distributed.node_test_support import envelope
from engine.executor import QueryProcessor
from engine.executor.native import to_native_schema


@pytest.fixture
def single_node(tmp_path, catalog_data):
    native = pytest.importorskip("quipudb_native")
    table = catalog_data["tables"][0]
    table["mode"] = "single"
    table["fragments"] = [
        {"fragment_id": "items_f0", "node_id": "n1", "lower": None, "upper": None}
    ]
    table["columns"].extend(
        [
            {"name": "p", "type": "POINT", "size": None},
            {"name": "v", "type": "VECTOR", "size": 2},
            {"name": "fecha", "type": "DATE", "size": None},
        ]
    )
    table["indexes"].extend(
        [
            {
                "name": "texto",
                "column": "nombre",
                "structure": "inverted_index",
                "supports_range": False,
            },
            {"name": "espacio", "column": "p", "structure": "rtree", "supports_range": False},
            {"name": "vectores", "column": "v", "structure": "hnsw", "supports_range": False},
        ]
    )
    catalog = DistributedCatalog.model_validate(catalog_data)
    directory = tmp_path / "single"
    directory.mkdir()
    db = native.Database(directory / "catalogo.txt")
    db.create_table(to_native_schema(catalog.table("items").bound_schema, native), "heap")
    qp = QueryProcessor(db)
    for i, name, x, y in [
        (1, "algebra quipu", 1, 0),
        (2, "historia inca", 0, 1),
        (3, "algebra antigua", 0.8, 0.2),
    ]:
        qp.execute(
            f"INSERT INTO items VALUES ({i}, '{name}', POINT({i}, {i}), "
            f"VECTOR({x}, {y}), DATE '2026-11-12')"
        )
    for index in catalog.table("items").indexes:
        if index.structure == "hnsw":
            db.create_hnsw_index("items", index.name, index.column, native.VectorMetric.COSINE)
        else:
            db.create_index("items", index.name, index.column, index.structure)
    db.flush()
    db.close("items")
    del qp, db
    service = FragmentService(catalog, "n1", directory)
    try:
        yield service
    finally:
        service.close()


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM items ORDER BY id",
        "SELECT nombre, COUNT(*) FROM items GROUP BY nombre",
        "SELECT id, SCORE() FROM items WHERE MATCH(nombre, 'algebra')",
        "SELECT * FROM items WHERE distancia(p, POINT(1,1), EUCLIDEAN) <= 1",
        "SELECT * FROM items WHERE DENTRO(p, POLYGON(POINT(0,0),POINT(0,4),POINT(4,0)))",
        "SELECT * FROM items ORDER BY distancia(p, POINT(1,1)) LIMIT 2",
        "SELECT * FROM items WHERE SIMILAR_TO(v, VECTOR(1,0), k=2) USING HNSW WITH METRIC=cosine",
        "EXPLAIN SELECT * FROM items WHERE id=1",
        "EXPLAIN ANALYZE SELECT * FROM items WHERE id=1",
    ],
)
def test_misma_respuesta_local_y_por_nodo(single_node, sql):
    native = pytest.importorskip("quipudb_native")
    db = native.Database(single_node.directory / "catalogo.txt")
    expected = to_response(QueryProcessor(db).execute(sql)).model_dump(mode="json")
    db.flush()
    for name in db.open_tables():
        db.close(name)
    del db
    with TestClient(create_node_app(single_node)) as client:
        response = client.post("/internal/v1/fragments/items_f0/execute", json=envelope(sql))
        assert response.status_code == 200, response.text
        result = response.json()["result"]
        for field in (
            "columns",
            "column_types",
            "rows",
            "affected_rows",
            "spatial_context",
            "is_explain",
        ):
            assert result[field] == expected[field]
        assert result["plan"] is not None


def test_single_borrado_por_columna_y_reapertura_hnsw(single_node):
    with TestClient(create_node_app(single_node)) as client:
        response = client.post(
            "/internal/v1/fragments/items_f0/execute",
            json=envelope("DELETE FROM items WHERE nombre='historia inca'"),
        )
        assert response.status_code == 200
        assert response.json()["result"]["affected_rows"] == 1
        scan = client.post(
            "/internal/v1/fragments/items_f0/scan", json=envelope("SELECT * FROM items")
        )
        assert scan.json()["error"]["code"] == "UNSUPPORTED_DISTRIBUTED"
    with TestClient(create_node_app(single_node)) as client:
        response = client.post(
            "/internal/v1/fragments/items_f0/execute",
            json=envelope(
                "SELECT id FROM items WHERE SIMILAR_TO(v, VECTOR(0,1), k=3) USING HNSW WITH METRIC=cosine"
            ),
        )
        assert response.status_code == 200, response.text
        assert sorted(row[0] for row in response.json()["result"]["rows"]) == [1, 3]


def test_multimedia_extrae_audio_real_con_vocabulario_local(single_node):
    np = pytest.importorskip("numpy")
    sf = pytest.importorskip("soundfile")
    native = pytest.importorskip("quipudb_native")
    from engine.multimedia.features import build_vocabulary, describe

    root = single_node.directory / "multimedia"
    root.mkdir()
    time = np.arange(16000) / 16000
    paths = [root / "consulta.wav", root / "otra.wav"]
    for path, freq in zip(paths, (440, 1300), strict=True):
        sf.write(path, 0.5 * np.sin(2 * np.pi * freq * time), 16000)
    vocabulary = build_vocabulary([describe(path) for path in paths], k=2, seed=277)
    vocabulary.save(root / "vocabulario.npz")
    single_node.media_vocabularies = {"items": "vocabulario.npz"}
    sql = "SELECT * FROM items WHERE SIMILAR_TO('consulta.wav', k=2) USING HNSW WITH METRIC=cosine"
    db = native.Database(single_node.directory / "catalogo.txt")
    qp = QueryProcessor(db, media_root=root, media_vocabularies=single_node.media_vocabularies)
    expected = to_response(qp.execute(sql)).model_dump(mode="json")
    assert expected["rows"]
    db.flush()
    db.close("items")
    del qp, db
    with TestClient(create_node_app(single_node)) as client:
        response = client.post("/internal/v1/fragments/items_f0/execute", json=envelope(sql))
        assert response.status_code == 200, response.text
        assert response.json()["result"]["rows"] == expected["rows"]


def test_single_ivf_real_conserva_resultado(single_node):
    native = pytest.importorskip("quipudb_native")
    data = single_node.catalog.model_dump(mode="json")
    for index in data["tables"][0]["indexes"]:
        if index["name"] == "vectores":
            index["structure"] = "ivf"
    single_node.catalog = DistributedCatalog.model_validate(data)
    db = native.Database(single_node.directory / "catalogo.txt")
    db.drop_index("items", "vectores")
    qp = QueryProcessor(db)
    qp.execute("CREATE INDEX vectores ON items (v) USING IVF")
    sql = "SELECT * FROM items WHERE SIMILAR_TO(v, VECTOR(1,0), k=2) USING IVF WITH METRIC=cosine"
    expected = to_response(qp.execute(sql)).model_dump(mode="json")
    db.flush()
    db.close("items")
    del qp, db
    with TestClient(create_node_app(single_node)) as client:
        response = client.post("/internal/v1/fragments/items_f0/execute", json=envelope(sql))
        assert response.status_code == 200, response.text
        assert response.json()["result"]["rows"] == expected["rows"]
