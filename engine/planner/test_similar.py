"""Seleccion de la estructura solicitada y planes vectoriales serializables."""

import pytest

from engine.parser import parse_sql
from engine.parser.semantic import bind_select
from engine.parser.test_similar import QUERY, SCHEMA
from engine.planner.explain import explain_select
from engine.planner.optimizer import AccessRoute, IndexMetadata, TableMetadata, optimize_select
from engine.planner.plan import Op, Plan, Step, Structure


@pytest.mark.parametrize("kind", [Structure.HNSW, Structure.IVF])
@pytest.mark.parametrize("indexed", [True, False])
def test_elige_estructura_o_busqueda_exacta(kind, indexed):
    indexes = tuple(
        IndexMetadata(structure.value, 2, structure, False)
        for structure in (
            Structure.HNSW,
            Structure.IVF,
        )
        if indexed or structure is not kind
    )
    sql = QUERY.replace("HNSW", kind.value.upper()) + " WITH METRIC=dot LIMIT 2"
    physical = optimize_select(
        bind_select(parse_sql(sql), SCHEMA),
        TableMetadata(
            "fotos",
            Structure.HEAP,
            indexes,
        ),
    )
    assert physical.route is (AccessRoute.VECTOR_KNN if indexed else AccessRoute.VECTOR_SCAN)
    plan = explain_select(physical, sql, sql)
    step = next(step for step in plan.root.walk() if step.op is Op.KNN_SEARCH)
    assert step.structure is (kind if indexed else Structure.HEAP)
    assert step.metric == "dot"
    assert all(step.time_ms == 0 and step.stats.pages_read == 0 for step in plan.root.walk())
    assert Plan.from_dict(plan.to_dict()).to_dict() == plan.to_dict()


def test_plan_viejo_sin_metric_sigue_leyendose():
    assert Step.from_dict({"op": "scan", "structure": "heap"}).metric is None


def test_order_by_ordena_los_k_vecinos_no_toda_la_tabla():
    sql = QUERY + " ORDER BY id DESC LIMIT 1"
    physical = optimize_select(
        bind_select(parse_sql(sql), SCHEMA), TableMetadata("fotos", Structure.HEAP)
    )
    steps = explain_select(physical, sql, sql).root.walk()
    assert [step.op for step in steps] == [Op.KNN_SEARCH, Op.FETCH, Op.SORT, Op.LIMIT]
