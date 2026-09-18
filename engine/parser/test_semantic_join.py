"""Semantica del JOIN: arbol de fuentes, concatenacion y resolucion de nombres."""

from __future__ import annotations

import pytest

from engine.parser.ast import SelectStatement, SqlTypeName
from engine.parser.bound_ast import (
    BoundColumn,
    BoundJoinRef,
    BoundSchema,
    BoundTableRef,
    join_output_schema,
)
from engine.parser.errors import SQLSemanticError
from engine.parser.parser import parse_sql
from engine.parser.semantic import bind_select

_ALUMNOS = BoundSchema(
    "alumnos",
    (
        BoundColumn("codigo", SqlTypeName.INT, None),
        BoundColumn("nombre", SqlTypeName.VARCHAR, 32),
        BoundColumn("promedio", SqlTypeName.DOUBLE, None),
    ),
    0,
)

_CURSOS = BoundSchema(
    "cursos",
    (
        BoundColumn("codigo", SqlTypeName.INT, None),
        BoundColumn("curso", SqlTypeName.VARCHAR, 40),
    ),
    0,
)

_CATALOGO = {"alumnos": _ALUMNOS, "cursos": _CURSOS}

_JOIN = "FROM alumnos JOIN cursos ON alumnos.codigo = cursos.codigo"


def _bind(sql: str):
    statement = parse_sql(sql)
    assert isinstance(statement, SelectStatement)
    return bind_select(statement, _CATALOGO, sql)


# ---------------------------------------------------------------------------
# Concatenacion de esquemas
# ---------------------------------------------------------------------------


def test_solo_se_prefijan_las_columnas_que_colisionan() -> None:
    schema = join_output_schema(_ALUMNOS, _CURSOS)

    assert schema.table_name == "alumnos_cursos"
    assert [column.name for column in schema.columns] == [
        "alumnos.codigo",
        "nombre",
        "promedio",
        "cursos.codigo",
        "curso",
    ]


def test_la_concatenacion_conserva_tipos_y_longitudes() -> None:
    schema = join_output_schema(_ALUMNOS, _CURSOS)

    assert schema.columns[1].data_type is SqlTypeName.VARCHAR
    assert schema.columns[1].length == 32
    assert schema.columns[4].length == 40
    assert schema.record_size == _ALUMNOS.record_size + _CURSOS.record_size


def test_la_salida_de_un_join_no_tiene_clave_primaria_util() -> None:
    assert join_output_schema(_ALUMNOS, _CURSOS).key_column == 0


def test_sin_colisiones_ninguna_columna_se_prefija() -> None:
    otros = BoundSchema("otros", (BoundColumn("x", SqlTypeName.INT, None),), 0)

    schema = join_output_schema(_ALUMNOS, otros)

    assert [column.name for column in schema.columns] == [
        "codigo",
        "nombre",
        "promedio",
        "x",
    ]


# ---------------------------------------------------------------------------
# El arbol de fuentes
# ---------------------------------------------------------------------------


def test_bind_select_construye_el_arbol_y_deriva_el_esquema_de_la_fuente() -> None:
    bound = _bind(f"SELECT * {_JOIN}")

    assert isinstance(bound.source, BoundJoinRef)
    assert isinstance(bound.source.left, BoundTableRef)
    assert isinstance(bound.source.right, BoundTableRef)
    assert bound.source.left.schema is _ALUMNOS
    assert bound.source.right.schema is _CURSOS
    assert bound.schema == join_output_schema(_ALUMNOS, _CURSOS)


def test_las_columnas_del_on_se_indexan_contra_el_esquema_de_su_lado() -> None:
    bound = _bind(f"SELECT * {_JOIN}")

    assert isinstance(bound.source, BoundJoinRef)
    assert bound.source.left_column.index == 0
    assert bound.source.right_column.index == 0


def test_el_wildcard_expande_las_columnas_de_los_dos_lados() -> None:
    bound = _bind(f"SELECT * {_JOIN}")

    assert [projection.index for projection in bound.projections] == [0, 1, 2, 3, 4]


def test_una_tabla_sola_sigue_dando_el_esquema_de_la_tabla() -> None:
    bound = _bind("SELECT * FROM alumnos")

    assert isinstance(bound.source, BoundTableRef)
    assert bound.schema == _ALUMNOS


# ---------------------------------------------------------------------------
# Resolucion de nombres
# ---------------------------------------------------------------------------


def test_una_columna_de_un_solo_lado_no_necesita_calificarse() -> None:
    bound = _bind(f"SELECT nombre, curso {_JOIN}")

    assert [projection.index for projection in bound.projections] == [1, 4]


def test_el_calificador_elige_el_lado_de_una_columna_repetida() -> None:
    izquierda = _bind(f"SELECT alumnos.codigo {_JOIN}")
    derecha = _bind(f"SELECT cursos.codigo {_JOIN}")

    assert izquierda.projections[0].index == 0
    assert derecha.projections[0].index == 3


def test_el_where_y_el_order_by_tambien_resuelven_contra_el_join() -> None:
    bound = _bind(f"SELECT nombre {_JOIN} WHERE alumnos.promedio >= 14 ORDER BY nombre DESC")

    assert bound.where is not None
    assert bound.where.column.index == 2
    assert bound.order_by is not None
    assert bound.order_by.column.index == 1


def test_el_group_by_agrupa_por_una_columna_del_lado_derecho() -> None:
    bound = _bind(f"SELECT curso, COUNT(*) {_JOIN} GROUP BY curso")

    assert bound.group_by is not None
    assert bound.group_by.column.index == 4


def test_la_columna_enlazada_lleva_el_nombre_que_vera_la_cabecera() -> None:
    bound = _bind(f"SELECT alumnos.codigo {_JOIN}")

    assert bound.projections[0].column.name == "alumnos.codigo"


# ---------------------------------------------------------------------------
# Errores
# ---------------------------------------------------------------------------


def test_una_columna_repetida_sin_calificar_es_ambigua() -> None:
    sql = f"SELECT codigo {_JOIN}"

    with pytest.raises(SQLSemanticError) as caught:
        _bind(sql)

    assert "ambigua" in caught.value.message
    assert "alumnos" in caught.value.message
    assert "cursos" in caught.value.message
    assert sql[caught.value.span.start : caught.value.span.end] == "codigo"


def test_un_calificador_de_una_tabla_ausente_lista_las_disponibles() -> None:
    with pytest.raises(SQLSemanticError) as caught:
        _bind(f"SELECT notas.codigo {_JOIN}")

    assert "notas" in caught.value.message
    assert "alumnos, cursos" in caught.value.message


def test_una_columna_que_no_existe_en_la_tabla_calificada_falla() -> None:
    with pytest.raises(SQLSemanticError) as caught:
        _bind(f"SELECT cursos.promedio {_JOIN}")

    assert "promedio" in caught.value.message
    assert "cursos" in caught.value.message


def test_el_on_exige_que_los_dos_lados_tengan_el_mismo_tipo() -> None:
    with pytest.raises(SQLSemanticError) as caught:
        _bind("SELECT * FROM alumnos JOIN cursos ON alumnos.nombre = cursos.codigo")

    assert "no se puede juntar" in caught.value.message
    assert "VARCHAR" in caught.value.message
    assert "INT" in caught.value.message


def test_una_tabla_desconocida_en_el_from_lista_los_esquemas_disponibles() -> None:
    with pytest.raises(SQLSemanticError) as caught:
        _bind("SELECT * FROM notas JOIN cursos ON notas.codigo = cursos.codigo")

    assert "notas" in caught.value.message
    assert "alumnos, cursos" in caught.value.message


def test_el_orden_de_los_operandos_del_on_no_importa() -> None:
    directo = _bind("SELECT * FROM alumnos JOIN cursos ON alumnos.codigo = cursos.codigo")
    cruzado = _bind("SELECT * FROM alumnos JOIN cursos ON cursos.codigo = alumnos.codigo")

    assert isinstance(directo.source, BoundJoinRef)
    assert isinstance(cruzado.source, BoundJoinRef)
    assert cruzado.source.left_column.index == directo.source.left_column.index
    assert cruzado.source.right_column.index == directo.source.right_column.index
    # Sin prefijo: las columnas del ON viven en el esquema de SU lado, no en la
    # concatenacion. Es lo que ExternalJoin recibe como left_column/right_column.
    assert cruzado.source.left_column.column.name == "codigo"
    assert cruzado.source.right_column.column.name == "codigo"


def test_el_on_admite_columnas_sin_calificar_cuando_no_hay_duda() -> None:
    bound = _bind("SELECT * FROM alumnos JOIN cursos ON codigo = codigo")

    assert isinstance(bound.source, BoundJoinRef)
    assert bound.source.left_column.index == 0
    assert bound.source.right_column.index == 0


def test_el_on_contra_una_columna_inexistente_se_explica() -> None:
    with pytest.raises(SQLSemanticError) as caught:
        _bind("SELECT * FROM alumnos JOIN cursos ON alumnos.codigo = cursos.inexistente")

    assert "inexistente" in caught.value.message
