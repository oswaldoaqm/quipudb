"""Seleccion de rutas para DENTRO(columna, POLYGON(...))."""

from engine.parser import DeleteStatement, SelectStatement, parse_sql
from engine.parser.ast import SqlTypeName
from engine.parser.bound_ast import BoundColumn, BoundSchema
from engine.parser.semantic import bind_delete, bind_select
from engine.planner.explain import explain_select
from engine.planner.optimizer import (
    AccessRoute,
    IndexMetadata,
    TableMetadata,
    optimize_delete,
    optimize_select,
)
from engine.planner.plan import Op, Structure

_SCHEMA = BoundSchema(
    "tiendas",
    (
        BoundColumn("id", SqlTypeName.INT, None),
        BoundColumn("ubicacion", SqlTypeName.POINT, None),
    ),
    0,
)
_WHERE = (
    "DENTRO(ubicacion, POLYGON(POINT(-12.2, -77.2), POINT(-12.2, -76.9), "
    "POINT(-11.9, -77.0)))"
)
_RTREE = IndexMetadata("por_ubicacion", 1, Structure.RTREE, supports_range=False)


def _select(source: str):
    statement = parse_sql(source)
    assert isinstance(statement, SelectStatement)
    return bind_select(statement, _SCHEMA, source)


def test_dentro_usa_el_rtree_sin_filtro_residual() -> None:
    plan = optimize_select(
        _select(f"SELECT * FROM tiendas WHERE {_WHERE}"),
        TableMetadata("tiendas", Structure.HEAP, (_RTREE,)),
    )

    assert plan.route is AccessRoute.RTREE_POLYGON
    assert plan.index == _RTREE
    assert plan.residual_filter is False


def test_dentro_sin_rtree_cae_a_scan_mas_filtro() -> None:
    plan = optimize_select(
        _select(f"SELECT * FROM tiendas WHERE {_WHERE}"),
        TableMetadata("tiendas", Structure.HEAP),
    )

    assert plan.route is AccessRoute.SCAN
    assert plan.index is None
    assert plan.residual_filter is True


def test_dentro_no_usa_un_indice_que_no_es_rtree() -> None:
    otro = IndexMetadata("por_id", 0, Structure.BPLUS_UNCLUSTERED, supports_range=True)

    plan = optimize_select(
        _select(f"SELECT * FROM tiendas WHERE {_WHERE}"),
        TableMetadata("tiendas", Structure.HEAP, (otro,)),
    )

    assert plan.route is AccessRoute.SCAN


def test_explain_de_dentro_muestra_polygon_search() -> None:
    source = f"SELECT * FROM tiendas WHERE {_WHERE}"
    plan = optimize_select(_select(source), TableMetadata("tiendas", Structure.HEAP, (_RTREE,)))

    ops = [step.op for step in explain_select(plan, source, source).root.walk()]

    assert Op.POLYGON_SEARCH in ops
    assert Op.FETCH in ops


def test_delete_con_dentro_recorre_para_conservar_los_rid() -> None:
    source = f"DELETE FROM tiendas WHERE {_WHERE}"
    statement = parse_sql(source)
    assert isinstance(statement, DeleteStatement)

    plan = optimize_delete(
        bind_delete(statement, _SCHEMA, source),
        TableMetadata("tiendas", Structure.HEAP, (_RTREE,)),
    )

    assert plan.route is AccessRoute.SCAN
    assert plan.residual_filter is True
