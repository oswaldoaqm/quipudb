"""Pruebas de la descripcion de planes fisicos sin ejecutar operadores."""

from engine.parser import ExplainStatement, SqlTypeName, parse_sql
from engine.parser.bound_ast import BoundColumn, BoundSchema
from engine.parser.semantic import bind_select
from engine.planner.explain import explain_select
from engine.planner.optimizer import IndexMetadata, TableMetadata, optimize_select
from engine.planner.plan import Op, Stats, Structure

_SCHEMA = BoundSchema(
    "alumnos",
    (
        BoundColumn("id", SqlTypeName.INT, None),
        BoundColumn("nombre", SqlTypeName.VARCHAR, 20),
    ),
    0,
)


def test_explain_describe_indice_fetch_y_proyeccion_sin_estadisticas() -> None:
    source = "EXPLAIN SELECT nombre FROM alumnos WHERE nombre = 'Ada'"
    statement = parse_sql(source)
    assert isinstance(statement, ExplainStatement)
    bound = bind_select(statement.statement, _SCHEMA, source)
    index = IndexMetadata(
        "por_nombre",
        1,
        Structure.EXTENDIBLE_HASH,
        supports_range=False,
    )
    physical = optimize_select(
        bound,
        TableMetadata("alumnos", Structure.HEAP, (index,), rows=100),
    )
    query = source[statement.statement.span.start : statement.statement.span.end]

    plan = explain_select(physical, query, source, planning_ms=0.25)

    assert [step.op for step in plan.root.walk()] == [
        Op.INDEX_SEARCH,
        Op.FETCH,
        Op.PROJECT,
    ]
    assert plan.root.walk()[0].structure is Structure.EXTENDIBLE_HASH
    assert "por_nombre" in plan.root.walk()[0].detail
    assert plan.query == "SELECT nombre FROM alumnos WHERE nombre = 'Ada'"
    assert plan.time_ms == 0.25
    assert all(step.stats == Stats() for step in plan.root.walk())
    assert all(step.time_ms == 0.0 for step in plan.root.walk())


def test_explain_describe_filtro_y_ordenamiento_externo() -> None:
    source = "EXPLAIN SELECT nombre FROM alumnos WHERE nombre > 'M' ORDER BY nombre DESC"
    statement = parse_sql(source)
    assert isinstance(statement, ExplainStatement)
    bound = bind_select(statement.statement, _SCHEMA, source)
    physical = optimize_select(
        bound,
        TableMetadata("alumnos", Structure.HEAP, rows=100),
    )

    plan = explain_select(
        physical,
        source[statement.statement.span.start : statement.statement.span.end],
        source,
    )

    assert [step.op for step in plan.root.walk()] == [
        Op.SCAN,
        Op.FILTER,
        Op.SORT,
        Op.PROJECT,
    ]
    assert plan.root.walk()[2].structure is Structure.EXTERNAL_SORT
    assert "DESC" in plan.root.walk()[2].detail


def test_explain_describe_group_by_y_posible_reuso_del_orden() -> None:
    source = "EXPLAIN SELECT nombre, COUNT(*) FROM alumnos GROUP BY nombre ORDER BY nombre"
    statement = parse_sql(source)
    assert isinstance(statement, ExplainStatement)
    bound = bind_select(statement.statement, _SCHEMA, source)
    physical = optimize_select(
        bound,
        TableMetadata("alumnos", Structure.HEAP, rows=100),
    )

    plan = explain_select(
        physical,
        source[statement.statement.span.start : statement.statement.span.end],
        source,
    )

    assert [step.op for step in plan.root.walk()] == [
        Op.SCAN,
        Op.GROUP,
        Op.SORT,
        Op.PROJECT,
    ]
    assert "fallback" in plan.root.walk()[1].detail
    assert "reutilizar" in plan.root.walk()[2].detail


def test_explain_describe_join_sin_ejecutarlo() -> None:
    cursos = BoundSchema(
        "cursos",
        (
            BoundColumn("curso_id", SqlTypeName.INT, None),
            BoundColumn("alumno_id", SqlTypeName.INT, None),
        ),
        0,
    )
    source = "EXPLAIN SELECT * FROM alumnos JOIN cursos ON alumnos.id = cursos.alumno_id"
    statement = parse_sql(source)
    assert isinstance(statement, ExplainStatement)
    bound = bind_select(
        statement.statement,
        {"alumnos": _SCHEMA, "cursos": cursos},
        source,
    )
    physical = optimize_select(
        bound,
        {
            "alumnos": TableMetadata("alumnos", Structure.HEAP, rows=100),
            "cursos": TableMetadata("cursos", Structure.HEAP, rows=200),
        },
    )

    plan = explain_select(
        physical,
        source[statement.statement.span.start : statement.statement.span.end],
        source,
    )

    assert [step.op for step in plan.root.walk()] == [Op.SCAN, Op.SCAN, Op.JOIN]
    assert plan.root.structure is Structure.EXTERNAL_HASH
    assert "HASH" in plan.root.detail
