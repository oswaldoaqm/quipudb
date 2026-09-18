"""Pruebas E2E del JOIN contra el core compilado (issue #96)."""

from __future__ import annotations

import pytest

from engine.executor import QueryProcessor

quipudb = pytest.importorskip(
    "quipudb_native",
    reason="los bindings no estan compilados: cmake -DQUIPUDB_BUILD_PYTHON=ON",
)


@pytest.fixture()
def db(tmp_path):
    return quipudb.Database(tmp_path / "catalogo.txt")


def _poblar(processor: QueryProcessor, alumnos: int = 4, cursos: int = 4) -> None:
    processor.execute(
        "CREATE TABLE alumnos (codigo INT PRIMARY KEY, nombre VARCHAR(16), "
        "promedio DOUBLE) USING HEAP"
    )
    processor.execute(
        "CREATE TABLE cursos (id INT PRIMARY KEY, alumno INT, curso VARCHAR(16)) USING HEAP"
    )
    for i in range(alumnos):
        processor.execute(
            f"INSERT INTO alumnos VALUES ({i}, 'a{i}', {10.0 + i})"
        )
    for i in range(cursos):
        processor.execute(f"INSERT INTO cursos VALUES ({i}, {i % alumnos}, 'c{i}')")


_ON = "FROM alumnos JOIN cursos ON alumnos.codigo = cursos.alumno"


def test_un_join_devuelve_el_producto_de_las_coincidencias(db):
    processor = QueryProcessor(db)
    _poblar(processor)

    result = processor.execute(f"SELECT * {_ON}")

    assert result.columns == ("codigo", "nombre", "promedio", "id", "alumno", "curso")
    esperado = {(i, f"a{i}", 10.0 + i, i, i, f"c{i}") for i in range(4)}
    assert set(result.rows) == esperado


def test_las_columnas_homonimas_salen_prefijadas_por_su_tabla(db):
    processor = QueryProcessor(db)
    processor.execute("CREATE TABLE a (id INT PRIMARY KEY, x INT) USING HEAP")
    processor.execute("CREATE TABLE b (id INT PRIMARY KEY, y INT) USING HEAP")
    processor.execute("INSERT INTO a VALUES (1, 10)")
    processor.execute("INSERT INTO b VALUES (1, 20)")

    result = processor.execute("SELECT * FROM a JOIN b ON a.id = b.id")

    assert result.columns == ("a.id", "x", "b.id", "y")
    assert result.rows == ((1, 10, 1, 20),)


def test_el_plan_lleva_un_paso_join_con_sus_dos_hijos(db):
    processor = QueryProcessor(db)
    _poblar(processor)

    result = processor.execute(f"SELECT * {_ON}")
    plan = result.plan.to_dict()

    assert plan["root"]["op"] == "join"
    assert len(plan["root"]["children"]) == 2
    assert plan["root"]["structure"] == "external_hash"
    assert "hash join" in plan["root"]["detail"]


def test_el_detalle_del_plan_dice_las_filas_de_cada_lado(db):
    processor = QueryProcessor(db)
    _poblar(processor, alumnos=4, cursos=6)

    detalle = processor.execute(f"SELECT * {_ON}").plan.to_dict()["root"]["detail"]

    assert "4 x 6" in detalle
    assert "filas externas 4" in detalle


def test_un_join_sin_coincidencias_devuelve_vacio_sin_error(db):
    processor = QueryProcessor(db)
    processor.execute("CREATE TABLE a (id INT PRIMARY KEY, x INT) USING HEAP")
    processor.execute("CREATE TABLE b (id INT PRIMARY KEY, x INT) USING HEAP")
    processor.execute("INSERT INTO a VALUES (1, 10)")
    processor.execute("INSERT INTO b VALUES (2, 20)")

    result = processor.execute("SELECT a.x, b.x FROM a JOIN b ON a.id = b.id")

    assert result.rows == ()
    # `x` esta en los dos lados, asi que la cabecera la prefija por su tabla.
    assert result.columns == ("a.x", "b.x")


def test_las_claves_repetidas_en_los_dos_lados_dan_el_producto(db):
    processor = QueryProcessor(db)
    processor.execute("CREATE TABLE a (id INT PRIMARY KEY, k INT) USING HEAP")
    processor.execute("CREATE TABLE b (id INT PRIMARY KEY, k INT) USING HEAP")
    for i in range(3):
        processor.execute(f"INSERT INTO a VALUES ({i}, 7)")
    for i in range(4):
        processor.execute(f"INSERT INTO b VALUES ({i}, 7)")

    result = processor.execute("SELECT a.id, b.id FROM a JOIN b ON a.k = b.k")

    assert len(result.rows) == 12


def test_el_where_de_un_lado_se_aplica_al_join(db):
    processor = QueryProcessor(db)
    _poblar(processor)

    result = processor.execute(f"SELECT nombre, curso {_ON} WHERE alumnos.promedio >= 12")

    assert {fila[0] for fila in result.rows} == {"a2", "a3"}


def test_el_where_del_lado_derecho_tambien(db):
    processor = QueryProcessor(db)
    _poblar(processor)

    result = processor.execute(f"SELECT nombre {_ON} WHERE cursos.curso = 'c1'")

    assert result.rows == (("a1",),)


def test_order_by_sobre_el_resultado_de_un_join(db):
    processor = QueryProcessor(db)
    _poblar(processor)

    result = processor.execute(f"SELECT nombre {_ON} ORDER BY nombre DESC")

    assert [fila[0] for fila in result.rows] == ["a3", "a2", "a1", "a0"]


def test_group_by_sobre_el_resultado_de_un_join(db):
    processor = QueryProcessor(db)
    processor.execute("CREATE TABLE a (id INT PRIMARY KEY, k INT) USING HEAP")
    processor.execute("CREATE TABLE b (id INT PRIMARY KEY, k INT, etiqueta VARCHAR(8)) USING HEAP")
    processor.execute("INSERT INTO a VALUES (1, 7)")
    processor.execute("INSERT INTO a VALUES (2, 7)")
    processor.execute("INSERT INTO b VALUES (1, 7, 'uno')")

    result = processor.execute(
        "SELECT etiqueta, COUNT(*) FROM a JOIN b ON a.k = b.k GROUP BY etiqueta"
    )

    assert result.rows == (("uno", 2),)


def test_un_indice_sobre_la_columna_de_join_da_el_mismo_resultado(db):
    processor = QueryProcessor(db)
    _poblar(processor)
    db.create_index("cursos", "por_alumno", "alumno", quipudb.kind.EXTENDIBLE_HASH)

    con_indice = processor.execute(f"SELECT nombre, curso {_ON}")
    raiz = con_indice.plan.to_dict()["root"]

    # Con una proyeccion, la raiz es el project y el join es su hijo.
    assert raiz["op"] == "project"
    assert raiz["children"][0]["op"] == "join"
    # Con pocas filas AUTO puede elegir cualquiera de los dos caminos; lo que
    # esta prueba fija es que el resultado no depende de esa eleccion.
    assert {(f[0], f[1]) for f in con_indice.rows} == {(f"a{i}", f"c{i}") for i in range(4)}


def test_una_tabla_inexistente_en_el_join_se_dice_con_su_nombre(db):
    from engine.parser import SQLSemanticError

    processor = QueryProcessor(db)
    _poblar(processor)

    with pytest.raises(SQLSemanticError, match="notas"):
        processor.execute("SELECT * FROM alumnos JOIN notas ON alumnos.codigo = notas.alumno")


def test_una_consulta_de_una_sola_tabla_no_cambia_de_plan(db):
    processor = QueryProcessor(db)
    _poblar(processor)

    plan = processor.execute("SELECT * FROM alumnos").plan.to_dict()

    assert plan["root"]["op"] == "scan"
