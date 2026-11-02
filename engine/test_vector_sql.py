"""Una columna VECTOR recorrida entera por SQL (issue #180).

El parser, el codec y los bindings se prueban cada uno por su lado, y aun
asi `CREATE TABLE ... VECTOR(3)` no funcionaba: el mapeo de tipos del
ejecutor, que esta entre todos ellos, no conocia el tipo. Estas pruebas
cierran ese hueco recorriendo el camino completo, que es el unico que el
usuario ve.

Se saltan enteras si los bindings no estan compilados; el CI SI los compila.
"""

from __future__ import annotations

import pytest

quipudb = pytest.importorskip(
    "quipudb_native",
    reason="los bindings no estan compilados: cmake -DQUIPUDB_BUILD_PYTHON=ON",
)

from engine.executor.processor import QueryProcessor
from engine.parser.ast import SqlTypeName
from engine.parser.errors import SQLSemanticError

CREAR = "CREATE TABLE fotos (id INT PRIMARY KEY, ruta VARCHAR(20), v VECTOR(3)) USING HEAP"


@pytest.fixture()
def qp(tmp_path):
    procesador = QueryProcessor(quipudb.Database(tmp_path / "catalogo.txt"))
    procesador.execute(CREAR)
    return procesador


# ---------------------------------------------------------------------------
# El camino completo
# ---------------------------------------------------------------------------


def test_se_puede_crear_una_tabla_con_columna_vectorial(tmp_path) -> None:
    # Esto es lo que moria con KeyError antes de mapear el tipo.
    procesador = QueryProcessor(quipudb.Database(tmp_path / "catalogo.txt"))
    assert procesador.execute(CREAR).affected_rows == 0


def test_se_puede_insertar_y_leer_un_vector(qp) -> None:
    qp.execute("INSERT INTO fotos VALUES (1, 'a.jpg', VECTOR(0.5, -1.25, 2.0))")
    resultado = qp.execute("SELECT * FROM fotos")
    assert resultado.rows == ((1, "a.jpg", (0.5, -1.25, 2.0)),)


def test_el_tipo_de_la_columna_llega_al_resultado(qp) -> None:
    # Es lo que el Panel de Archivos y la tabla de resultados usan para
    # saber como dibujar la celda.
    assert qp.execute("SELECT * FROM fotos").column_types[2] is SqlTypeName.VECTOR


def test_las_componentes_enteras_se_guardan_como_numeros(qp) -> None:
    qp.execute("INSERT INTO fotos VALUES (1, 'a.jpg', VECTOR(1, 2, 3))")
    assert qp.execute("SELECT * FROM fotos").rows[0][2] == (1.0, 2.0, 3.0)


def test_el_vector_convive_con_los_metadatos_de_la_fila(qp) -> None:
    # Criterio 4 del issue, ya por SQL y no solo en el esquema.
    qp.execute("INSERT INTO fotos VALUES (7, 'paisaje.jpg', VECTOR(1, 2, 3))")
    fila = qp.execute("SELECT * FROM fotos").rows[0]
    assert fila[0] == 7
    assert fila[1] == "paisaje.jpg"


def test_se_puede_filtrar_por_otra_columna_y_traer_el_vector(qp) -> None:
    qp.execute("INSERT INTO fotos VALUES (1, 'a.jpg', VECTOR(1, 1, 1))")
    qp.execute("INSERT INTO fotos VALUES (2, 'b.jpg', VECTOR(2, 2, 2))")
    assert qp.execute("SELECT * FROM fotos WHERE id = 2").rows == ((2, "b.jpg", (2.0, 2.0, 2.0)),)


def test_se_puede_proyectar_solo_el_vector(qp) -> None:
    qp.execute("INSERT INTO fotos VALUES (1, 'a.jpg', VECTOR(0.25, 0.5, 0.75))")
    assert qp.execute("SELECT v FROM fotos").rows == (((0.25, 0.5, 0.75),),)


def test_varias_filas_conservan_cada_una_su_vector(qp) -> None:
    for i in range(1, 4):
        qp.execute(f"INSERT INTO fotos VALUES ({i}, 'f{i}.jpg', VECTOR({i}, {i}, {i}))")
    vectores = [fila[2] for fila in qp.execute("SELECT * FROM fotos").rows]
    assert sorted(vectores) == [(1.0, 1.0, 1.0), (2.0, 2.0, 2.0), (3.0, 3.0, 3.0)]


# ---------------------------------------------------------------------------
# El vector sobrevive al disco
# ---------------------------------------------------------------------------


def test_la_tabla_con_vector_se_puede_reabrir(tmp_path) -> None:
    ruta = tmp_path / "catalogo.txt"
    db = quipudb.Database(ruta)
    procesador = QueryProcessor(db)
    procesador.execute(CREAR)
    procesador.execute("INSERT INTO fotos VALUES (1, 'a.jpg', VECTOR(0.5, -1.25, 2.0))")
    db.flush()
    del procesador, db

    otra = QueryProcessor(quipudb.Database(ruta))
    assert otra.execute("SELECT * FROM fotos").rows == ((1, "a.jpg", (0.5, -1.25, 2.0)),)


# ---------------------------------------------------------------------------
# Lo que SQL no deja hacer
# ---------------------------------------------------------------------------


def test_un_vector_de_otra_dimension_no_entra(qp) -> None:
    with pytest.raises(SQLSemanticError, match="componentes"):
        qp.execute("INSERT INTO fotos VALUES (1, 'a.jpg', VECTOR(1, 2))")


@pytest.mark.parametrize("estructura", ["BPLUS", "HASH"])
def test_no_se_puede_indexar_un_vector_por_igualdad_ni_orden(qp, estructura: str) -> None:
    # Un vector se compara por similitud: un B+ o un hash sobre el no
    # responderia ninguna consulta util. Los que le sirven son IVF y HNSW
    # (#183 a #186), que todavia no existen.
    with pytest.raises(SQLSemanticError, match="similitud"):
        qp.execute(f"CREATE INDEX ix ON fotos (v) USING {estructura}")


def test_un_indice_de_texto_sobre_un_vector_tampoco(qp) -> None:
    with pytest.raises(SQLSemanticError):
        qp.execute("CREATE INDEX ix ON fotos (v) USING FULLTEXT")


def test_un_rtree_sobre_un_vector_tampoco(qp) -> None:
    with pytest.raises(SQLSemanticError):
        qp.execute("CREATE INDEX ix ON fotos (v) USING RTREE")
