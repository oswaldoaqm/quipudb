"""Pruebas del plan de ejecucion (issue #4).

Los tres planes de aqui son los mismos ejemplos del ADR 0002 y los tres casos
que el Panel de Plan de Ejecucion (#37) tiene que soportar: busqueda puntual,
busqueda por rango y ORDER BY. Si el ADR cambia, cambian estas pruebas.
"""

import json

import pytest

from engine.planner.plan import Op, Plan, Stats, Step, Structure


def plan_busqueda_puntual() -> Plan:
    return Plan(
        query="SELECT * FROM alumnos WHERE codigo = 42",
        time_ms=0.41,
        root=Step(
            op=Op.SEARCH,
            structure=Structure.BPLUS_CLUSTERED,
            table="alumnos",
            column="codigo",
            detail="codigo = 42",
            stats=Stats(pages_read=3, records_examined=1, records_returned=1),
            time_ms=0.30,
        ),
    )


def plan_rango_por_indice_secundario() -> Plan:
    return Plan(
        query="SELECT * FROM alumnos WHERE promedio BETWEEN 15 AND 17",
        time_ms=2.10,
        root=Step(
            op=Op.FETCH,
            structure=Structure.HEAP,
            table="alumnos",
            detail="lee 12 registros por RID",
            stats=Stats(pages_read=9, records_examined=12, records_returned=12),
            time_ms=1.20,
            children=[
                Step(
                    op=Op.INDEX_RANGE,
                    structure=Structure.BPLUS_UNCLUSTERED,
                    table="alumnos",
                    column="promedio",
                    detail="promedio en [15, 17]",
                    stats=Stats(pages_read=4, records_examined=12, records_returned=12),
                    time_ms=0.60,
                )
            ],
        ),
    )


def plan_order_by() -> Plan:
    return Plan(
        query="SELECT * FROM alumnos ORDER BY nombre",
        time_ms=48.0,
        root=Step(
            op=Op.SORT,
            structure=Structure.EXTERNAL_SORT,
            table="alumnos",
            column="nombre",
            detail="k-way merge, 4 runs",
            stats=Stats(
                pages_read=80, pages_written=80, records_examined=10000, records_returned=10000
            ),
            time_ms=31.0,
            children=[
                Step(
                    op=Op.SCAN,
                    structure=Structure.HEAP,
                    table="alumnos",
                    stats=Stats(pages_read=40, records_examined=10000, records_returned=10000),
                    time_ms=14.0,
                )
            ],
        ),
    )


@pytest.mark.parametrize(
    "plan", [plan_busqueda_puntual(), plan_rango_por_indice_secundario(), plan_order_by()]
)
def test_serializa_y_vuelve_igual(plan: Plan) -> None:
    ida = plan.to_dict()
    vuelta = Plan.from_dict(json.loads(json.dumps(ida)))
    assert vuelta == plan
    assert vuelta.to_dict() == ida


def test_el_json_tiene_las_claves_que_consume_el_panel() -> None:
    d = plan_rango_por_indice_secundario().to_dict()
    assert set(d) == {"query", "time_ms", "totals", "root"}
    assert set(d["root"]) == {
        "op",
        "structure",
        "table",
        "column",
        "detail",
        "stats",
        "time_ms",
        "children",
    }
    assert set(d["root"]["stats"]) == {
        "pages_read",
        "pages_written",
        "records_examined",
        "records_returned",
    }
    # Los valores de op y structure son las cadenas del ADR, no nombres de Python.
    assert d["root"]["op"] == "fetch"
    assert d["root"]["children"][0]["structure"] == "bplus_unclustered"


def test_los_totales_suman_el_subarbol() -> None:
    plan = plan_rango_por_indice_secundario()
    assert plan.to_dict()["totals"] == {
        "pages_read": 13,
        "pages_written": 0,
        "records_examined": 24,
        "records_returned": 24,
    }
    assert plan.root.subtree_time_ms() == pytest.approx(1.80)
    assert plan.time_ms >= plan.root.subtree_time_ms()


def test_orden_de_ejecucion_es_hijos_primero() -> None:
    ops = [s.op for s in plan_order_by().root.walk()]
    assert ops == [Op.SCAN, Op.SORT]


def test_estructuras_usadas_ignora_memoria_y_no_repite() -> None:
    plan = plan_order_by()
    plan.root = Step(
        op=Op.LIMIT,
        structure=Structure.MEMORY,
        detail="10",
        children=[plan.root],
    )
    assert plan.structures_used() == [Structure.HEAP, Structure.EXTERNAL_SORT]


def test_las_estructuras_del_core_coinciden_con_kind() -> None:
    # Copia literal de las constantes kind:: de core/include/quipudb/catalog/table.hpp.
    del_core = {"heap", "sequential", "bplus_clustered", "bplus_unclustered", "extendible_hash"}
    assert del_core <= {s.value for s in Structure}


def test_op_desconocida_falla_al_deserializar() -> None:
    with pytest.raises(ValueError):
        Step.from_dict({"op": "magia", "structure": "heap"})
