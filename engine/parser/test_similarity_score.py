"""SIMILARITY_SCORE y su orden, sin core ni acceso a archivos (#189)."""

import pytest

from engine.parser import ScoreCall, ScoreKind, ScoreOrderBy, parse_sql
from engine.parser.errors import SQLParseError, SQLSemanticError, SQLUnsupportedError
from engine.parser.semantic import bind_select
from engine.parser.test_similar import SCHEMA
from engine.planner.explain import explain_select
from engine.planner.optimizer import TableMetadata, optimize_select
from engine.planner.plan import Op, Structure

WHERE = " WHERE SIMILAR_TO('audio_query.mp3', k=5) USING IVF WITH METRIC=euclidean"


@pytest.mark.parametrize("alias", ["score", "relevancia", "distancia_euclidea", "id"])
def test_alias_del_puntaje_gana_en_order_by(alias):
    sql = (
        f"SELECT ruta, SIMILARITY_SCORE() AS {alias} FROM fotos" + WHERE + f" ORDER BY {alias} DESC"
    )
    statement = parse_sql(sql)
    assert isinstance(statement.projections[1], ScoreCall)
    assert statement.projections[1].kind is ScoreKind.SIMILARITY
    bound = bind_select(statement, SCHEMA, sql)
    assert bound.score.name == alias
    assert bound.score.position == 1
    assert bound.order_by is None
    assert bound.score_order.kind is ScoreKind.SIMILARITY


def test_sin_alias_y_en_primera_posicion():
    sql = "SELECT SIMILARITY_SCORE(), ruta FROM fotos" + WHERE + " ORDER BY similarity_score"
    bound = bind_select(parse_sql(sql), SCHEMA)
    assert (bound.score.name, bound.score.position) == ("similarity_score", 0)
    assert bound.score_order is not None


def test_estrella_y_puntaje():
    bound = bind_select(
        parse_sql("SELECT *, SIMILARITY_SCORE() AS score FROM fotos" + WHERE), SCHEMA
    )
    assert bound.wildcard
    assert bound.score.position == 3


def test_orden_directo_sin_proyectar_puntaje():
    sql = "SELECT ruta FROM fotos" + WHERE + " ORDER BY SIMILARITY_SCORE() DESC"
    statement = parse_sql(sql)
    assert isinstance(statement.order_by, ScoreOrderBy)
    bound = bind_select(statement, SCHEMA)
    assert bound.score is None
    assert bound.score_order.kind is ScoreKind.SIMILARITY


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT SIMILARITY_SCORE() FROM fotos",
        "SELECT SIMILARITY_SCORE() FROM fotos WHERE id=1",
        "SELECT id FROM fotos ORDER BY SIMILARITY_SCORE()",
        "SELECT SIMILARITY_SCORE() FROM fotos WHERE MATCH(ruta, 'algo')",
    ],
)
def test_sin_similar_to_es_error_ubicado(sql):
    with pytest.raises(SQLSemanticError, match="necesita SIMILAR_TO") as caught:
        bind_select(parse_sql(sql), SCHEMA, sql)
    assert "SIMILARITY_SCORE" in sql[caught.value.span.start : caught.value.span.end]


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT SCORE(), SIMILARITY_SCORE() FROM fotos",
        "SELECT SIMILARITY_SCORE(), SIMILARITY_SCORE() FROM fotos",
        "SELECT SIMILARITY_SCORE() FROM fotos" + WHERE + " ORDER BY SCORE()",
        "SELECT SCORE() FROM fotos" + WHERE,
    ],
)
def test_no_mezcla_puntajes_y_no_duplica_la_columna(sql):
    with pytest.raises(SQLSemanticError):
        bind_select(parse_sql(sql), SCHEMA, sql)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT SIMILARITY_SCORE(1) FROM fotos",
        "SELECT SIMILARITY_SCORE FROM fotos",
        "SELECT id FROM fotos ORDER BY SIMILARITY_SCORE(1)",
        "SELECT SIMILARITY_SCORE() AS FROM fotos",
    ],
)
def test_funcion_sin_argumentos_y_alias_valido(sql):
    with pytest.raises(SQLParseError):
        parse_sql(sql)


def test_puntaje_no_pasa_por_ordenamiento_externo():
    sql = "SELECT SIMILARITY_SCORE() FROM fotos" + WHERE + " ORDER BY id"
    with pytest.raises(SQLUnsupportedError, match="External"):
        bind_select(parse_sql(sql), SCHEMA)


@pytest.mark.parametrize("metric", ["euclidean", "cosine", "dot"])
@pytest.mark.parametrize("direction", ["ASC", "DESC"])
def test_explain_declara_convencion_y_orden_en_memoria(metric, direction):
    sql = "SELECT ruta, SIMILARITY_SCORE() AS score FROM fotos" + WHERE.replace("euclidean", metric)
    sql += f" ORDER BY score {direction} LIMIT 2"
    bound = bind_select(parse_sql(sql), SCHEMA)
    physical = optimize_select(bound, TableMetadata("fotos", Structure.HEAP))
    assert not physical.external_sort
    steps = explain_select(physical, sql, sql).root.walk()
    assert [step.op for step in steps] == [Op.KNN_SEARCH, Op.FETCH, Op.SORT, Op.PROJECT, Op.LIMIT]
    sort = steps[2]
    assert sort.structure is Structure.MEMORY
    assert ("menor es mejor" if metric == "euclidean" else "mayor es mejor") in sort.detail
    assert all(step.stats.pages_read == 0 and step.time_ms == 0 for step in steps)
    assert "SIMILARITY_SCORE" in steps[3].detail
