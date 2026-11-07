"""SIMILAR_TO con un VECTOR literal contra el core real (#251).

No necesita numpy ni ExtractFeatures: el vector viene escrito en el SQL, que es
lo que hace la busqueda hibrida de Quipucamayoc con el embedding de una
pregunta.
"""

from __future__ import annotations

import pytest

from engine.executor import QueryProcessor
from engine.parser.errors import SQLSemanticError
from engine.planner.plan import Op, Structure

q = pytest.importorskip("quipudb_native", reason="se necesitan los bindings compilados")

VECTORES = {1: (1.0, 0.0, 0.0), 2: (0.0, 1.0, 0.0), 3: (0.6, 0.8, 0.0), 4: (0.0, 0.0, 1.0)}


@pytest.fixture()
def processor(tmp_path):
    p = QueryProcessor(q.Database(tmp_path / "catalogo.txt"))
    p.execute("CREATE TABLE docs (id INT PRIMARY KEY, emb VECTOR(3)) USING HEAP")
    p.execute(";".join(f"INSERT INTO docs VALUES ({i}, VECTOR{v})" for i, v in VECTORES.items()))
    return p


def _knn(resultado):
    return next(paso for paso in resultado.plan.root.walk() if paso.op is Op.KNN_SEARCH)


@pytest.mark.parametrize("con_indice", [False, True])
def test_busca_por_un_vector_literal_con_y_sin_hnsw(processor, con_indice):
    if con_indice:
        processor.execute("CREATE INDEX docs_hnsw ON docs (emb) USING HNSW WITH METRIC=cosine")

    resultado = processor.execute(
        "SELECT id, SIMILARITY_SCORE() AS score FROM docs "
        "WHERE SIMILAR_TO(emb, VECTOR(0.8, 0.6, 0), k=2) USING HNSW WITH METRIC=cosine"
    )

    assert [fila[0] for fila in resultado.rows] == [3, 1]
    assert resultado.rows[0][1] == pytest.approx(0.96)
    paso = _knn(resultado)
    assert paso.structure is (Structure.HNSW if con_indice else Structure.HEAP)
    assert "consulta VECTOR literal de 3 componentes" in paso.detail


def test_explain_con_vector_literal_no_busca_archivos(processor):
    processor.execute("CREATE INDEX docs_hnsw ON docs (emb) USING HNSW WITH METRIC=cosine")

    resultado = processor.execute(
        "EXPLAIN SELECT id FROM docs WHERE SIMILAR_TO(VECTOR(0, 0, 1), k=1) USING HNSW"
    )

    assert _knn(resultado).structure is Structure.HNSW


def test_un_vector_de_otra_dimension_es_error_ubicado(processor):
    sql = "SELECT id FROM docs WHERE SIMILAR_TO(VECTOR(1, 0), k=1) USING HNSW"

    with pytest.raises(SQLSemanticError, match="tiene 2 componentes") as e:
        processor.execute(sql)

    assert sql[e.value.span.start : e.value.span.end] == "VECTOR(1, 0)"


def test_las_consultas_con_archivo_siguen_yendo_por_la_ruta_del_archivo(processor):
    with pytest.raises(SQLSemanticError, match="ruta"):
        processor.execute("SELECT id FROM docs WHERE SIMILAR_TO('foto.png', k=1) USING HNSW")
