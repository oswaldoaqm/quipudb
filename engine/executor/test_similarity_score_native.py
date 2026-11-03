"""Puntajes naturales, orden y filas alineadas en SQL/HTTP con core real."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from engine.api.main import create_app
from engine.executor import QueryProcessor
from engine.executor.test_similar_native import VECTORS, nearest_step, q
from engine.executor.test_similar_native import dataset as vector_dataset
from engine.parser.ast import SqlTypeName
from engine.planner.plan import Op, Structure

dataset = vector_dataset
SQL = (
    "SELECT id, rasgos, SIMILARITY_SCORE() AS score FROM fotos "
    "WHERE SIMILAR_TO('consulta.png', k=4) USING {kind} WITH METRIC={metric}"
)


@pytest.mark.parametrize("kind", ["HNSW", "IVF", "exact"])
@pytest.mark.parametrize("metric", ["euclidean", "cosine", "dot"])
@pytest.mark.parametrize("direction", ["ASC", "DESC"])
def test_valor_natural_orden_y_correspondencia(dataset, kind, metric, direction):
    _database, processor, _calls = dataset
    if kind != "exact":
        suffix = f" WITH METRIC={metric}" if kind == "HNSW" else ""
        processor.execute(f"CREATE INDEX vecinos ON fotos (rasgos) USING {kind}" + suffix)
    sql = SQL.format(kind="IVF" if kind == "exact" else kind, metric=metric)
    result = processor.execute(sql + f" ORDER BY score {direction}")
    assert result.columns == ("id", "rasgos", "score")
    assert result.column_types[-1] is SqlTypeName.DOUBLE
    assert len(result.rows) == 4
    enum = getattr(q.VectorMetric, "DOT_PRODUCT" if metric == "dot" else metric.upper())
    for identifier, vector, score in result.rows:
        assert vector == pytest.approx(VECTORS[identifier])
        assert score == pytest.approx(q.vector_similarity(VECTORS[0], vector, enum))
    scores = [row[-1] for row in result.rows]
    assert scores == sorted(scores, reverse=direction == "DESC")
    assert nearest_step(result).structure.value == ("heap" if kind == "exact" else kind.lower())
    sort = next(step for step in result.plan.root.walk() if step.op is Op.SORT)
    assert sort.structure is Structure.MEMORY
    assert sort.stats.records_returned == 4
    limited = processor.execute(sql + f" ORDER BY score {direction} LIMIT 1")
    assert limited.rows == result.rows[:1]


def test_euclidea_desc_no_promete_el_mejor_primero(dataset):
    _database, processor, _calls = dataset
    result = processor.execute(SQL.format(kind="IVF", metric="euclidean") + " ORDER BY score DESC")
    assert result.rows[-1][0] == 0
    assert result.rows[-1][-1] == 0.0
    assert result.rows[0][-1] > 0.0


@pytest.mark.parametrize(
    "projection, order, names",
    [
        ("SIMILARITY_SCORE(), id", "similarity_score", ("similarity_score", "id")),
        (
            "* , SIMILARITY_SCORE() AS relevancia",
            "relevancia",
            ("id", "ruta", "rasgos", "relevancia"),
        ),
        ("id", "SIMILARITY_SCORE()", ("id",)),
    ],
)
def test_proyeccion_y_orden_directo(dataset, projection, order, names):
    _database, processor, _calls = dataset
    sql = SQL.format(kind="IVF", metric="cosine").replace(
        "id, rasgos, SIMILARITY_SCORE() AS score",
        projection,
    )
    result = processor.execute(sql + f" ORDER BY {order} DESC")
    assert result.columns == names
    assert len(result.rows) == 4


@pytest.mark.parametrize("k", [0, 1, 100])
def test_no_pierde_puntajes_con_k_y_sin_order_by(dataset, k):
    _database, processor, _calls = dataset
    sql = SQL.format(kind="IVF", metric="cosine").replace("k=4", f"k={k}")
    result = processor.execute(sql)
    assert len(result.rows) == min(k, 4)
    assert all(isinstance(row[-1], float) for row in result.rows)


def test_explain_no_extrae_y_analyze_no_devuelve_filas(dataset):
    _database, processor, calls = dataset
    sql = SQL.format(kind="IVF", metric="dot") + " ORDER BY score DESC LIMIT 2"
    planned = processor.execute("EXPLAIN " + sql)
    assert calls == []
    assert all(step.time_ms == 0 for step in planned.plan.root.walk())
    measured = processor.execute("EXPLAIN ANALYZE " + sql)
    assert len(calls) == 1
    assert measured.is_explain and not measured.rows
    assert (
        next(step for step in measured.plan.root.walk() if step.op is Op.SORT).structure
        is Structure.MEMORY
    )


def test_http_alias_double_y_error_ubicado(dataset, tmp_path):
    database, processor, _calls = dataset
    app = create_app(processor=processor, database=database, native=q, media_root=tmp_path)
    with TestClient(app) as client:
        sql = SQL.format(kind="IVF", metric="cosine") + " ORDER BY score DESC"
        response = client.post("/query", json={"sql": sql})
        assert response.status_code == 200, response.text
        assert response.json()["columns"][-1] == "score"
        assert response.json()["column_types"][-1] == "DOUBLE"
        assert isinstance(response.json()["rows"][0][-1], float)
        sql = "\nSELECT SIMILARITY_SCORE() FROM fotos"
        response = client.post("/query", json={"sql": sql})
        assert response.status_code == 400
        assert response.json()["line"] == 2 and response.json()["column"] == 8


@pytest.mark.parametrize("extension", ["png", "wav", "mp3"])
@pytest.mark.parametrize("kind", ["IVF", "HNSW", "exact"])
def test_audio_e_imagen_reales_con_puntaje(tmp_path, extension, kind):
    from engine.multimedia.features import build_vocabulary, describe, extract_features

    pytest.importorskip("cv2" if extension == "png" else "soundfile")
    data = Path(__file__).parents[1] / "multimedia" / "datos"
    query = tmp_path / ("audio_query.mp3" if extension == "mp3" else f"ejemplo.{extension}")
    if extension == "mp3":
        import soundfile as sf

        if "MPEG_LAYER_III" not in sf.available_subtypes("MP3"):
            pytest.skip("libsndfile no permite generar el MP3 temporal de prueba")
        samples, rate = sf.read(data / "ejemplo.wav")
        sf.write(query, samples, rate, format="MP3", subtype="MPEG_LAYER_III")
    else:
        query.write_bytes((data / query.name).read_bytes())
    descriptors = describe(query)
    vocabulary = build_vocabulary([descriptors[::2], descriptors[1::2]], 4, seed=189)
    vocabulary.save(tmp_path / "vocab.npz")
    vector = extract_features(query, vocabulary).tolist()
    database = q.Database(tmp_path / "catalogo.txt")
    processor = QueryProcessor(
        database, media_root=tmp_path, media_vocabularies={"canciones": "vocab.npz"}, vector_nprobe=16,
    )
    processor.execute(
        "CREATE TABLE canciones (id INT PRIMARY KEY, nombre VARCHAR(40), artista VARCHAR(40), rasgos VECTOR(4))"
    )
    database.table("canciones").insert([1, "original", "demo", vector])
    database.table("canciones").insert([2, "diferente", "demo", [value + 0.25 for value in vector]])
    if kind != "exact":
        processor.execute(
            f"CREATE INDEX vecinos ON canciones (rasgos) USING {kind} WITH METRIC=euclidean"
            if kind == "HNSW"
            else "CREATE INDEX vecinos ON canciones (rasgos) USING IVF"
        )
    sql = (
        "SELECT nombre, artista, SIMILARITY_SCORE() AS score FROM canciones WHERE "
        f"SIMILAR_TO('{query.name}', k=5) USING {'IVF' if kind == 'exact' else kind} "
        "WITH METRIC=euclidean ORDER BY score DESC"
    )
    result = processor.execute(sql)
    assert result.columns == ("nombre", "artista", "score")
    assert result.rows[0][0] == "diferente"
    assert result.rows[0][-1] == pytest.approx(0.5, abs=1e-6)
    assert result.rows[-1][-1] == pytest.approx(0.0, abs=1e-6)
