"""Combinación de scans (#279): orden, proyección, límite y respuestas inválidas."""

import pytest

from engine.api.schemas import QueryResponse
from engine.distributed.client import NodeFailure
from engine.distributed.merge import merge
from engine.distributed.router import route


def result(rows, columns=("id", "nombre"), types=("INT", "VARCHAR")) -> QueryResponse:
    return QueryResponse(columns=list(columns), column_types=list(types), rows=rows)


def combine(catalog, sql, f0_rows, f1_rows):
    scan = route(catalog, sql)
    by_id = {"items_f0": result(f0_rows), "items_f1": result(f1_rows)}
    return merge(scan, [(f, by_id[f.fragment_id]) for f in scan.fragments])


def test_order_by_y_limit_son_globales(node_catalog):
    response = combine(
        node_catalog,
        "SELECT nombre FROM items ORDER BY id DESC LIMIT 3",
        [[1, "a"], [9999, "b"], [5, "c"]],
        [[10000, "d"], [20000, "e"]],
    )
    assert response.rows == [["e"], ["d"], ["b"]]
    assert (response.columns, response.column_types) == (["nombre"], ["VARCHAR"])
    assert (response.spatial_context, response.is_explain) == (None, False)


def test_el_plan_describe_los_pasos_y_marca_un_nodo_sin_plan(node_catalog):
    scan = route(node_catalog, "SELECT nombre FROM items ORDER BY id LIMIT 2")
    f0, f1 = scan.fragments
    con_plan = result([[1, "a"]]).model_copy(
        update={
            "plan": {
                "query": scan.sql,
                "time_ms": 0.5,
                "totals": {},
                "root": {
                    "op": "scan",
                    "structure": "heap",
                    "table": "items",
                    "column": None,
                    "detail": "recorre la tabla",
                    "stats": {"pages_read": 3, "records_examined": 1, "records_returned": 1},
                    "time_ms": 0.4,
                    "children": [],
                },
            }
        }
    )
    plan = merge(scan, [(f0, con_plan), (f1, result([[10000, "b"]]))], sql="consulta").plan
    assert plan["query"] == "consulta"
    limit = plan["root"]
    sort = limit["children"][0]["children"][0]
    assert [limit["op"], limit["children"][0]["op"], sort["op"]] == ["limit", "project", "sort"]
    assert limit["detail"] == "primeras 2 filas del total combinado"
    n1, n2 = sort["children"]
    assert (n1["detail"], n1["stats"]["pages_read"], n1["time_ms"]) == (
        "n1 · items_f0: recorre la tabla",
        3,
        0.4,
    )
    assert n2["detail"] == "n2 · items_f1: el nodo no devolvió su plan"
    assert plan["totals"]["pages_read"] == 3


def test_sin_order_by_se_concatena_en_orden_de_rango(node_catalog):
    response = combine(node_catalog, "SELECT * FROM items", [[7, "a"]], [[10001, "b"]])
    assert response.rows == [[7, "a"], [10001, "b"]]


def test_conserva_valores_repetidos_legitimos(node_catalog):
    response = combine(
        node_catalog,
        "SELECT nombre FROM items ORDER BY id",
        [[1, "igual"], [2, "igual"]],
        [[10000, "igual"]],
    )
    assert response.rows == [["igual"]] * 3


def test_respuestas_vacias(node_catalog):
    response = combine(node_catalog, "SELECT id FROM items WHERE id > 5", [], [])
    assert (response.columns, response.column_types, response.rows) == (["id"], ["INT"], [])


def test_sin_fragmentos_devuelve_tabla_vacia_tipada(node_catalog):
    scan = route(node_catalog, "SELECT nombre, id FROM items WHERE id < -2147483648")
    assert scan.fragments == ()
    response = merge(scan, [])
    assert (response.columns, response.column_types, response.rows) == (
        ["nombre", "id"],
        ["VARCHAR", "INT"],
        [],
    )


def test_limit_cero(node_catalog):
    response = combine(node_catalog, "SELECT * FROM items LIMIT 0", [[1, "a"]], [])
    assert response.rows == []


@pytest.mark.parametrize(
    ("f0", "message"),
    [
        (result([], columns=("id",), types=("INT",)), "columnas"),
        (result([], types=("INT", "INT")), "columnas"),
        (result([[1]]), "ancho"),
        (result([[10000, "ajena"]]), "no pertenece"),
        (result([[True, "bool"]]), "no pertenece"),
        (result([[3, "a"], [3, "b"]]), "dos veces"),
        (result([], columns=("id", "nombre")).model_copy(update={"is_explain": True}), "EXPLAIN"),
    ],
)
def test_una_respuesta_que_no_cumple_el_contrato_falla(node_catalog, f0, message):
    scan = route(node_catalog, "SELECT * FROM items")
    f0_fragment, f1_fragment = scan.fragments
    with pytest.raises(NodeFailure) as caught:
        merge(scan, [(f0_fragment, f0), (f1_fragment, result([]))])
    failure = caught.value
    assert (failure.code, failure.status, failure.node_id, failure.fragment_id) == (
        "INVALID_NODE_RESPONSE",
        502,
        "n1",
        "items_f0",
    )
    assert message in failure.message
