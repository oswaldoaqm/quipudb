"""Contrato SQL vectorial del #188, sin archivos ni bindings."""

from dataclasses import replace

import pytest

from engine.parser import IndexKind, SQLParseError, VectorMetric, parse_sql, parse_sql_script
from engine.parser.ast import SimilarCondition, SqlTypeName
from engine.parser.bound_ast import BoundColumn, BoundSchema
from engine.parser.errors import SQLSemanticError, SQLUnsupportedError
from engine.parser.semantic import bind_create_index, bind_delete, bind_select

SCHEMA = BoundSchema(
    "fotos",
    (
        BoundColumn("id", SqlTypeName.INT, None),
        BoundColumn("ruta", SqlTypeName.VARCHAR, 80),
        BoundColumn("rasgos", SqlTypeName.VECTOR, 3),
    ),
    0,
)
QUERY = "SELECT * FROM fotos WHERE SIMILAR_TO('consulta.jpg', k=10) USING HNSW"


@pytest.mark.parametrize("kind", ["HNSW", "IVF"])
@pytest.mark.parametrize("metric", ["euclidean", "cosine", "dot"])
def test_sintaxis_del_enunciado(kind, metric):
    sql = QUERY.replace("HNSW", kind) + f" WITH METRIC={metric}"
    where = parse_sql(sql).where
    assert isinstance(where, SimilarCondition)
    assert (where.query.value, where.k.value, where.kind, where.metric) == (
        "consulta.jpg",
        10,
        IndexKind(kind),
        VectorMetric(metric),
    )
    bound = bind_select(parse_sql(sql), SCHEMA).where
    assert bound.column.index == 2
    assert bound.vector is None


def test_columna_explicita_k_posicional_y_mayusculas():
    sql = "select ruta from fotos where similar_to(fotos.rasgos, 'a.jpg', 4) using ivf with metric=DOT"
    where = bind_select(parse_sql(sql), SCHEMA).where
    assert (where.column.index, where.k, where.metric) == (2, 4, VectorMetric.DOT)


def test_comentarios_multilinea_y_lotes_conservan_posicion():
    sql = QUERY + ";\nSELECT * FROM fotos WHERE\n SIMILAR_TO('a.jpg', /*top*/ k=0) USING IVF;"
    statements = parse_sql_script(sql)
    assert len(statements) == 2
    assert statements[1].where.query.span.line == 3
    assert statements[1].where.k.value == 0


@pytest.mark.parametrize(
    "suffix, fragment",
    [
        ("USING HASH", "HASH"),
        ("USING desconocido", "desconocido"),
        ("USING HNSW WITH METRIC=manhattan", "manhattan"),
        ("USING HNSW WITH METRIC='cosine'", "'cosine'"),
    ],
)
def test_estructura_y_metrica_desconocidas_tienen_ubicacion(suffix, fragment):
    sql = "SELECT * FROM fotos\nWHERE SIMILAR_TO('a.jpg', k=3) " + suffix
    with pytest.raises(SQLParseError) as caught:
        parse_sql(sql)
    assert caught.value.line == 2
    assert sql[caught.value.span.start :].startswith(fragment)


@pytest.mark.parametrize("argument", ["k=-1", "k=1.5", "k='3'", "k=true", "n=3", "k=", ""])
def test_k_invalido(argument):
    with pytest.raises(SQLParseError):
        parse_sql(f"SELECT * FROM fotos WHERE SIMILAR_TO('a.jpg', {argument}) USING HNSW")


@pytest.mark.parametrize("column", ["id", "ruta"])
def test_la_columna_debe_ser_vector(column):
    with pytest.raises(SQLSemanticError, match="VECTOR"):
        bind_select(parse_sql(QUERY.replace("'consulta.jpg'", f"{column}, 'consulta.jpg'")), SCHEMA)


def test_columna_ambigua_exige_nombre_explicito():
    schema = replace(SCHEMA, columns=SCHEMA.columns + (BoundColumn("otra", SqlTypeName.VECTOR, 3),))
    with pytest.raises(SQLSemanticError, match="explicita"):
        bind_select(parse_sql(QUERY), schema)
    assert bind_select(parse_sql(QUERY.replace("'consulta.jpg'", "otra, 'consulta.jpg'")), schema)


def test_sin_vector_se_rechaza():
    with pytest.raises(SQLSemanticError, match="VECTOR"):
        bind_select(parse_sql(QUERY), replace(SCHEMA, columns=SCHEMA.columns[:2]))


def test_delete_no_borra_por_similitud():
    with pytest.raises(SQLUnsupportedError, match="SELECT"):
        bind_delete(parse_sql(QUERY.replace("SELECT *", "DELETE")), SCHEMA)


def test_join_no_rankea_fuentes_compuestas():
    sql = QUERY.replace("FROM fotos", "FROM fotos JOIN otras ON fotos.id = otras.id")
    with pytest.raises(SQLUnsupportedError, match="JOIN"):
        bind_select(parse_sql(sql), {"fotos": SCHEMA, "otras": replace(SCHEMA, table_name="otras")})


def test_group_by_no_agrupa_vecinos():
    sql = QUERY.replace("SELECT *", "SELECT id, COUNT(*)") + " GROUP BY id"
    with pytest.raises(SQLUnsupportedError, match="GROUP BY"):
        bind_select(parse_sql(sql), SCHEMA)


@pytest.mark.parametrize("kind", ["IVF", "HNSW"])
def test_crear_indices_vectoriales(kind):
    bound = bind_create_index(
        parse_sql(f"CREATE INDEX vecinos ON fotos (rasgos) USING {kind}"), SCHEMA
    )
    assert bound.kind is IndexKind(kind)


@pytest.mark.parametrize("metric", ["cosine", "euclidean", "dot"])
def test_hnsw_con_metrica_fija(metric):
    statement = parse_sql(f"CREATE INDEX vecinos ON fotos (rasgos) USING HNSW WITH METRIC={metric}")
    assert statement.metric is VectorMetric(metric)


@pytest.mark.parametrize("kind", ["IVF", "HNSW"])
def test_indices_vectoriales_rechazan_escalar(kind):
    with pytest.raises(SQLSemanticError, match="VECTOR"):
        bind_create_index(parse_sql(f"CREATE INDEX vecinos ON fotos (ruta) USING {kind}"), SCHEMA)


# ---------------------------------------------------------------------------
# VECTOR literal en lugar de un archivo (#251)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT id FROM fotos WHERE SIMILAR_TO(rasgos, VECTOR(0.5, -0.25, 1), k=2) USING HNSW",
        "SELECT id FROM fotos WHERE SIMILAR_TO(VECTOR(0.5, -0.25, 1), 2) USING IVF",
    ],
)
def test_vector_literal_se_enlaza_sin_archivo(sql):
    where = bind_select(parse_sql(sql), SCHEMA).where

    assert where.column.index == 2
    assert where.query == ""
    assert where.vector == (0.5, -0.25, 1.0)
    assert where.k == 2


def test_vector_literal_de_otra_dimension_senala_la_componente_que_sobra():
    sql = "SELECT id FROM fotos WHERE SIMILAR_TO(rasgos, VECTOR(1, 2, 3, 4), k=1) USING HNSW"

    with pytest.raises(SQLSemanticError, match=r"tiene 4 componentes y la columna declara VECTOR\(3\)") as e:
        bind_select(parse_sql(sql), SCHEMA)

    assert sql[e.value.span.start : e.value.span.end] == "4"


def test_vector_literal_corto_senala_el_literal_entero():
    sql = "SELECT id FROM fotos WHERE SIMILAR_TO(VECTOR(1, 2), k=1) USING HNSW"

    with pytest.raises(SQLSemanticError, match="tiene 2 componentes") as e:
        bind_select(parse_sql(sql), SCHEMA)

    assert sql[e.value.span.start : e.value.span.end] == "VECTOR(1, 2)"


def test_vector_literal_mal_formado_es_error_de_parseo():
    with pytest.raises(SQLParseError, match="componente 2 de VECTOR"):
        parse_sql("SELECT id FROM fotos WHERE SIMILAR_TO(VECTOR(1, 'x', 3), k=1) USING HNSW")


def test_sin_archivo_ni_vector_el_error_dice_las_dos_formas():
    with pytest.raises(SQLParseError, match="archivo entre comillas o un VECTOR"):
        parse_sql("SELECT id FROM fotos WHERE SIMILAR_TO(rasgos, 42, k=1) USING HNSW")
