"""Planificacion fisica de un JOIN: hoja, sonda y empuje del predicado."""

from __future__ import annotations

import pytest

from engine.parser.ast import SelectStatement, SqlTypeName
from engine.parser.bound_ast import BoundColumn, BoundSchema
from engine.parser.parser import parse_sql
from engine.parser.semantic import bind_select
from engine.planner.optimizer import (
    AccessRoute,
    IndexMetadata,
    JoinStrategy,
    PhysicalJoin,
    PhysicalTableAccess,
    TableMetadata,
    optimize_select,
)
from engine.planner.plan import Structure

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
        BoundColumn("alumno", SqlTypeName.INT, None),
        BoundColumn("curso", SqlTypeName.VARCHAR, 40),
    ),
    0,
)
_ESQUEMAS = {"alumnos": _ALUMNOS, "cursos": _CURSOS}

_JOIN = "FROM alumnos JOIN cursos ON alumnos.codigo = cursos.alumno"


def _hash_index(column: int, name: str = "por_alumno") -> IndexMetadata:
    return IndexMetadata(name, column, Structure.EXTENDIBLE_HASH, supports_range=False)


def _plan(sql: str, alumnos: TableMetadata, cursos: TableMetadata):
    statement = parse_sql(sql)
    assert isinstance(statement, SelectStatement)
    bound = bind_select(statement, _ESQUEMAS, sql)
    return optimize_select(bound, {"alumnos": alumnos, "cursos": cursos})


def _heap(name: str, *indexes: IndexMetadata, rows: int | None = None) -> TableMetadata:
    return TableMetadata(name, Structure.HEAP, tuple(indexes), rows=rows)


# ---------------------------------------------------------------------------
# Forma del plan
# ---------------------------------------------------------------------------


def test_un_join_produce_un_nodo_con_las_dos_hojas() -> None:
    plan = _plan(f"SELECT * {_JOIN}", _heap("alumnos"), _heap("cursos"))

    assert isinstance(plan.source, PhysicalJoin)
    assert isinstance(plan.source.left, PhysicalTableAccess)
    assert isinstance(plan.source.right, PhysicalTableAccess)
    assert plan.source.left.table.name == "alumnos"
    assert plan.source.right.table.name == "cursos"
    assert plan.source.left_column == 0
    assert plan.source.right_column == 0


def test_pedir_una_hoja_sobre_un_join_falla_en_vez_de_mentir() -> None:
    plan = _plan(f"SELECT * {_JOIN}", _heap("alumnos"), _heap("cursos"))

    with pytest.raises(TypeError, match="es un join"):
        assert plan.access


def test_una_tabla_sola_sigue_dando_una_hoja() -> None:
    sql = "SELECT * FROM alumnos"
    statement = parse_sql(sql)
    assert isinstance(statement, SelectStatement)
    plan = optimize_select(bind_select(statement, _ALUMNOS, sql), _heap("alumnos"))

    assert isinstance(plan.source, PhysicalTableAccess)
    assert plan.route is AccessRoute.SCAN
    assert plan.table.name == "alumnos"


# ---------------------------------------------------------------------------
# Eleccion de estrategia
# ---------------------------------------------------------------------------


def test_sin_sonda_se_pide_hash_porque_es_el_unico_camino() -> None:
    plan = _plan(f"SELECT * {_JOIN}", _heap("alumnos"), _heap("cursos"))

    assert plan.source.strategy is JoinStrategy.HASH
    assert plan.source.probe is None
    assert plan.source.probe_on_primary_key is False


def test_un_indice_sobre_la_columna_de_join_habilita_auto_no_index_nested() -> None:
    indice = _hash_index(0)
    plan = _plan(f"SELECT * {_JOIN}", _heap("alumnos"), _heap("cursos", indice))

    # AUTO, no INDEX_NESTED: la eleccion la hace la regla medida del core con
    # la aritmetica de paginas exacta, no la mera existencia del indice.
    assert plan.source.strategy is JoinStrategy.AUTO
    assert plan.source.probe == indice


def test_un_indice_sobre_otra_columna_no_sirve_de_sonda() -> None:
    plan = _plan(f"SELECT * {_JOIN}", _heap("alumnos"), _heap("cursos", _hash_index(1)))

    assert plan.source.strategy is JoinStrategy.HASH
    assert plan.source.probe is None


def test_la_clave_primaria_del_lado_derecho_es_sonda_si_la_tabla_la_resuelve() -> None:
    cursos = TableMetadata("cursos", Structure.BPLUS_CLUSTERED, ())
    plan = _plan(f"SELECT * {_JOIN}", _heap("alumnos"), cursos)

    assert plan.source.probe_on_primary_key is True
    assert plan.source.probe is None
    assert plan.source.strategy is JoinStrategy.AUTO


def test_un_heap_no_resuelve_su_clave_primaria_y_no_es_sonda() -> None:
    plan = _plan(f"SELECT * {_JOIN}", _heap("alumnos"), _heap("cursos"))

    assert plan.source.probe_on_primary_key is False


# ---------------------------------------------------------------------------
# Filas del lado externo
# ---------------------------------------------------------------------------


def test_las_filas_del_lado_externo_salen_de_la_metadata() -> None:
    plan = _plan(f"SELECT * {_JOIN}", _heap("alumnos", rows=10_000), _heap("cursos"))

    assert plan.source.left_rows == 10_000


def test_sin_conteo_de_filas_el_lado_externo_es_desconocido() -> None:
    plan = _plan(f"SELECT * {_JOIN}", _heap("alumnos"), _heap("cursos"))

    assert plan.source.left_rows is None


# ---------------------------------------------------------------------------
# Empuje del predicado
# ---------------------------------------------------------------------------


def test_el_predicado_de_una_hoja_baja_a_esa_hoja() -> None:
    indice = _hash_index(2, "por_promedio")
    plan = _plan(
        f"SELECT * {_JOIN} WHERE alumnos.promedio = 15",
        _heap("alumnos", indice),
        _heap("cursos"),
    )

    assert plan.source.left.route is AccessRoute.INDEX_SEARCH
    assert plan.source.left.index == indice
    assert plan.source.right.route is AccessRoute.SCAN
    assert plan.source.residual_filter is False


def test_el_predicado_del_lado_derecho_baja_al_lado_derecho() -> None:
    plan = _plan(
        f"SELECT * {_JOIN} WHERE cursos.curso = 'BD2'",
        _heap("alumnos"),
        _heap("cursos"),
    )

    assert plan.source.left.route is AccessRoute.SCAN
    assert plan.source.right.route is AccessRoute.SCAN
    assert plan.source.right.residual_filter is True


def test_la_clave_primaria_del_lado_empujado_usa_la_ruta_de_tabla() -> None:
    alumnos = TableMetadata("alumnos", Structure.SEQUENTIAL, ())
    plan = _plan(f"SELECT * {_JOIN} WHERE alumnos.codigo = 7", alumnos, _heap("cursos"))

    assert plan.source.left.route is AccessRoute.TABLE_SEARCH


def test_el_indice_de_la_hoja_que_no_tiene_el_predicado_no_se_usa() -> None:
    plan = _plan(
        f"SELECT * {_JOIN} WHERE alumnos.promedio = 15",
        _heap("alumnos"),
        _heap("cursos", _hash_index(0)),
    )

    assert plan.source.right.route is AccessRoute.SCAN
    assert plan.source.right.index is None


def test_order_by_y_group_by_conviven_con_el_join() -> None:
    plan = _plan(
        f"SELECT curso, COUNT(*) {_JOIN} GROUP BY curso ORDER BY curso DESC",
        _heap("alumnos"),
        _heap("cursos"),
    )

    assert plan.external_sort is True
    assert plan.group_strategy is not None
    assert isinstance(plan.source, PhysicalJoin)


def test_falta_de_metadata_de_una_tabla_se_dice_con_su_nombre() -> None:
    sql = f"SELECT * {_JOIN}"
    statement = parse_sql(sql)
    assert isinstance(statement, SelectStatement)
    bound = bind_select(statement, _ESQUEMAS, sql)

    with pytest.raises(ValueError, match="cursos"):
        optimize_select(bound, {"alumnos": _heap("alumnos")})


def test_un_predicado_empujado_al_lado_derecho_anula_la_sonda() -> None:
    # El INL sondea la tabla, no el flujo filtrado: con un WHERE en este lado
    # devolveria filas ya descartadas. Sin sonda, el join va por hash.
    plan = _plan(
        f"SELECT * {_JOIN} WHERE cursos.curso = 'BD2'",
        _heap("alumnos"),
        _heap("cursos", _hash_index(0)),
    )

    assert plan.source.right.residual_filter is True
    assert plan.source.probe is None
    assert plan.source.strategy is JoinStrategy.HASH


def test_un_predicado_en_el_lado_izquierdo_no_estorba_a_la_sonda() -> None:
    indice = _hash_index(0)
    plan = _plan(
        f"SELECT * {_JOIN} WHERE alumnos.promedio = 15",
        _heap("alumnos"),
        _heap("cursos", indice),
    )

    assert plan.source.probe == indice
    assert plan.source.strategy is JoinStrategy.AUTO
