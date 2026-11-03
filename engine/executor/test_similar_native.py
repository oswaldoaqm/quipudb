"""SQL -> ExtractFeatures -> IVF/HNSW/scan -> filas y plan, con core real."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from engine.executor import QueryProcessor
from engine.parser.errors import SQLSemanticError
from engine.planner.plan import Op, Structure
from engine.transactions import TransactionError

q = pytest.importorskip("quipudb_native", reason="se necesitan los bindings compilados")
np = pytest.importorskip("numpy")

VECTORS = [(1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.6, 0.8, 0.0), (0.0, 0.0, 0.0)]
SQL = "SELECT * FROM fotos WHERE SIMILAR_TO('consulta.png', k=3) USING {kind} WITH METRIC={metric}"


@pytest.fixture()
def dataset(tmp_path, monkeypatch):
    from engine.multimedia import features

    vocabulary = features.build_vocabulary(
        [
            np.random.default_rng(188).normal(size=(8, 128)).astype(np.float32),
            np.random.default_rng(189).normal(size=(8, 128)).astype(np.float32),
        ],
        3,
        seed=188,
    )
    vocabulary.save(tmp_path / "vocabulario.npz")
    (tmp_path / "consulta.png").write_bytes(b"extraccion controlada")
    calls = []

    def extract(path, vocabulary, **options):
        calls.append((path, vocabulary.identifier, options))
        return np.array(VECTORS[0], dtype=np.float32)

    monkeypatch.setattr(features, "extract_features", extract)
    database = q.Database(tmp_path / "catalogo.txt")
    processor = QueryProcessor(
        database,
        media_root=tmp_path,
        media_vocabularies={"fotos": "vocabulario.npz"},
        vector_nprobe=16,
    )
    processor.execute("CREATE TABLE fotos (id INT PRIMARY KEY, ruta VARCHAR(80), rasgos VECTOR(3))")
    for index, vector in enumerate(VECTORS):
        processor.execute(f"INSERT INTO fotos VALUES ({index}, 'foto{index}.png', VECTOR{vector})")
    return database, processor, calls


def nearest_step(result):
    return next(step for step in result.plan.root.walk() if step.op is Op.KNN_SEARCH)


@pytest.mark.parametrize("kind", ["HNSW", "IVF"])
@pytest.mark.parametrize("metric", ["euclidean", "cosine", "dot"])
@pytest.mark.parametrize("indexed", [False, True])
def test_cada_estructura_y_metrica_ejecutan_la_busqueda(dataset, kind, metric, indexed):
    database, processor, calls = dataset
    if indexed:
        suffix = f" WITH METRIC={metric}" if kind == "HNSW" else ""
        processor.execute(f"CREATE INDEX vecinos ON fotos (rasgos) USING {kind}" + suffix)
    result = processor.execute(SQL.format(kind=kind, metric=metric))
    assert len(calls) == 1
    enum = getattr(q.VectorMetric, "DOT_PRODUCT" if metric == "dot" else metric.upper())
    table = database.table("fotos")
    expected = q.scan_k_nearest_vector(table, "rasgos", VECTORS[0], 3, enum)
    assert [row[0] for row in result.rows] == [table.read(match.rid)[0] for match in expected]
    step = nearest_step(result)
    assert step.metric == metric
    assert step.structure.value == (kind.lower() if indexed else "heap")
    assert step.stats.records_examined > 0
    assert all(row is not None for row in result.rows)


def test_using_elige_entre_ambos_indices_y_explain_no_extrae(dataset):
    _database, processor, calls = dataset
    processor.execute("CREATE INDEX grafo ON fotos (rasgos) USING HNSW")
    processor.execute("CREATE INDEX listas ON fotos (rasgos) USING IVF")
    for kind in ("HNSW", "IVF"):
        sql = SQL.format(kind=kind, metric="cosine")
        explain = processor.execute("EXPLAIN " + sql)
        assert calls == []
        assert explain.rows == ()
        assert nearest_step(explain).structure.value == kind.lower()
        assert all(step.stats.pages_read == 0 for step in explain.plan.root.walk())
    analyzed = processor.execute("EXPLAIN ANALYZE " + SQL.format(kind="IVF", metric="cosine"))
    assert len(calls) == 1
    assert analyzed.rows == ()
    assert analyzed.plan.root.subtree_stats().pages_read > 0


def test_un_indice_de_otro_tipo_no_reemplaza_el_pedido(dataset):
    _database, processor, _calls = dataset
    processor.execute("CREATE INDEX grafo ON fotos (rasgos) USING HNSW")
    result = processor.execute(SQL.format(kind="IVF", metric="cosine"))
    assert nearest_step(result).structure is Structure.HEAP


def test_metrica_hnsw_incompatible_se_senala_antes_de_extraer(dataset):
    _database, processor, calls = dataset
    processor.execute("CREATE INDEX grafo ON fotos (rasgos) USING HNSW")
    sql = SQL.format(kind="HNSW", metric="euclidean")
    with pytest.raises(SQLSemanticError, match="metrica") as caught:
        processor.execute(sql)
    assert sql[caught.value.span.start :] == "euclidean"
    assert calls == []


def test_elige_hnsw_compatible_entre_varios(dataset):
    _database, processor, _calls = dataset
    processor.execute("CREATE INDEX a_coseno ON fotos (rasgos) USING HNSW")
    processor.execute("CREATE INDEX b_euclideo ON fotos (rasgos) USING HNSW WITH METRIC=euclidean")
    result = processor.execute(SQL.format(kind="HNSW", metric="euclidean"))
    assert "b_euclideo" in nearest_step(result).detail


def test_vocabulario_de_otra_dimension_se_rechaza(dataset, tmp_path):
    database, _processor, _calls = dataset
    processor = QueryProcessor(
        database,
        media_root=tmp_path,
        media_vocabularies={"fotos": "vocabulario.npz"},
    )
    processor.execute("CREATE TABLE otros (id INT PRIMARY KEY, v VECTOR(2))")
    processor._vector_search.vocabularies["otros"] = "vocabulario.npz"
    with pytest.raises(SQLSemanticError, match="VECTOR\\(2\\)"):
        processor.execute(SQL.format(kind="IVF", metric="cosine").replace("fotos", "otros"))


def test_vocabulario_por_columna_tiene_prioridad(dataset):
    _database, processor, _calls = dataset
    processor._vector_search.vocabularies = {
        "fotos.rasgos": "vocabulario.npz",
        "fotos": "no_existe.npz",
    }
    assert processor.execute(SQL.format(kind="IVF", metric="cosine")).rows


@pytest.mark.parametrize("value", ["no_existe.png", "../fuera.png", ""])
def test_archivo_invalido_tiene_linea_y_columna(dataset, value):
    _database, processor, _calls = dataset
    sql = "\n" + SQL.format(kind="HNSW", metric="cosine").replace("consulta.png", value)
    with pytest.raises(SQLSemanticError) as caught:
        processor.execute(sql)
    assert caught.value.line == 2
    assert sql[caught.value.span.start] == "'"


def test_rechaza_symlinks_aun_dentro_de_la_raiz(dataset, tmp_path):
    _database, processor, _calls = dataset
    (tmp_path / "enlace.png").symlink_to(tmp_path / "consulta.png")
    with pytest.raises(SQLSemanticError, match="simbolicos"):
        processor.execute(
            SQL.format(kind="HNSW", metric="cosine").replace("consulta.png", "enlace.png")
        )


def test_falta_vocabulario_es_un_error_ubicado(dataset, tmp_path):
    database, _processor, _calls = dataset
    processor = QueryProcessor(database, media_root=tmp_path)
    with pytest.raises(SQLSemanticError, match="vocabulario"):
        processor.execute(SQL.format(kind="HNSW", metric="cosine"))


def test_fallback_exacto_tambien_funciona_en_secuencial(dataset):
    database, processor, _calls = dataset
    processor.execute(
        "CREATE TABLE secuencial (id INT PRIMARY KEY, rasgos VECTOR(3)) USING SEQUENTIAL"
    )
    for index, vector in enumerate(VECTORS):
        processor.execute(f"INSERT INTO secuencial VALUES ({index}, VECTOR{vector})")
    processor._vector_search.vocabularies["secuencial"] = "vocabulario.npz"
    result = processor.execute(
        SQL.format(kind="IVF", metric="cosine").replace("fotos", "secuencial")
    )
    assert nearest_step(result).structure is Structure.SEQUENTIAL
    assert result.rows[0][0] == 0
    with pytest.raises(SQLSemanticError, match="RID"):
        processor.execute("CREATE INDEX no_valido ON secuencial (rasgos) USING HNSW")
    assert len(database.table_info("secuencial").indexes) == 0


@pytest.mark.parametrize("kind", ["IVF", "HNSW"])
def test_indices_se_mantienen_con_insert_delete_y_rollback(dataset, kind):
    database, processor, _calls = dataset
    processor.execute(f"CREATE INDEX vecinos ON fotos (rasgos) USING {kind}")
    index = database.index("fotos", "vecinos")
    processor.execute("INSERT INTO fotos VALUES (10, 'nueva.png', VECTOR(0.9, 0.1, 0))")
    assert index.size() == 5
    processor.execute("DELETE FROM fotos WHERE id=10")
    assert index.size() == 4
    processor.execute("BEGIN TRANSACTION; DELETE FROM fotos WHERE id=0")
    assert index.size() == 3
    processor.rollback()
    assert index.size() == 4
    assert processor.execute(SQL.format(kind=kind, metric="cosine")).rows[0][0] == 0
    assert len(processor.execute("SELECT * FROM fotos").rows) == 4


@pytest.mark.parametrize("kind", ["IVF", "HNSW"])
def test_catalogo_e_indices_se_reabren(dataset, kind, tmp_path):
    database, processor, _calls = dataset
    processor.execute(f"CREATE INDEX vecinos ON fotos (rasgos) USING {kind}")
    database.flush()
    reopened = q.Database(tmp_path / "catalogo.txt")
    processor = QueryProcessor(
        reopened,
        media_root=tmp_path,
        media_vocabularies={"fotos": "vocabulario.npz"},
        vector_nprobe=16,
    )
    assert (
        nearest_step(processor.execute(SQL.format(kind=kind, metric="cosine"))).structure.value
        == kind.lower()
    )


def test_ivf_vacio_no_deja_indice_parcial(tmp_path):
    database = q.Database(tmp_path / "catalogo.txt")
    processor = QueryProcessor(database)
    processor.execute("CREATE TABLE fotos (id INT PRIMARY KEY, v VECTOR(3))")
    with pytest.raises(SQLSemanticError, match="carga los vectores"):
        processor.execute("CREATE INDEX listas ON fotos (v) USING IVF")
    assert len(database.table_info("fotos").indexes) == 0
    processor.execute("CREATE INDEX grafo ON fotos (v) USING HNSW")
    assert len(database.table_info("fotos").indexes) == 1
    processor.execute("BEGIN TRANSACTION")
    with pytest.raises(TransactionError):
        processor.execute("CREATE INDEX otro ON fotos (v) USING HNSW")
    processor.rollback()


@pytest.mark.parametrize("k", [0, 100, 10**100])
def test_k_y_limit_se_aplican_sin_overflow(dataset, k):
    _database, processor, _calls = dataset
    sql = SQL.format(kind="HNSW", metric="cosine").replace("k=3", f"k={k}")
    result = processor.execute(sql + " ORDER BY id DESC LIMIT 2")
    assert len(result.rows) == min(k, 2)
    assert [row[0] for row in result.rows] == sorted([row[0] for row in result.rows], reverse=True)


@pytest.mark.parametrize("extension", ["png", "wav"])
def test_extract_features_real_antes_de_buscar(tmp_path, extension):
    from engine.multimedia.features import build_vocabulary, describe, extract_features

    pytest.importorskip("cv2" if extension == "png" else "soundfile")
    fixture = Path(__file__).parents[1] / "multimedia" / "datos" / f"ejemplo.{extension}"
    query = tmp_path / fixture.name
    shutil.copyfile(fixture, query)
    descriptors = describe(query)
    vocabulary = build_vocabulary([descriptors[::2], descriptors[1::2]], 4, seed=188)
    vocabulary.save(tmp_path / "vocab.npz")
    vector = extract_features(query, vocabulary)
    database = q.Database(tmp_path / "catalogo.txt")
    processor = QueryProcessor(
        database, media_root=tmp_path, media_vocabularies={"objetos": "vocab.npz"}
    )
    processor.execute("CREATE TABLE objetos (id INT PRIMARY KEY, rasgos VECTOR(4))")
    database.table("objetos").insert([1, vector.tolist()])
    for kind in ("HNSW", "IVF"):
        processor.execute(f"CREATE INDEX por_{kind.lower()} ON objetos (rasgos) USING {kind}")
        result = processor.execute(
            f"SELECT id FROM objetos WHERE SIMILAR_TO('{query.name}', k=1) USING {kind} WITH METRIC=cosine"
        )
        assert result.rows == ((1,),)
        assert nearest_step(result).structure.value == kind.lower()
