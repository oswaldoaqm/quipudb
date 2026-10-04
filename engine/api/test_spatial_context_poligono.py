"""Contrato HTTP del contexto de DENTRO: no necesita los bindings."""

from engine.api.service import to_response
from engine.executor.result import PolygonContext, QueryResult, RadiusContext
from engine.parser.ast import ComparisonOperator, DistanceMetric, SqlTypeName
from engine.parser.bound_ast import PointValue

_VERTICES = (
    PointValue(-12.2, -77.2),
    PointValue(-12.2, -76.9),
    PointValue(-11.9, -77.0),
)


def test_poligono_viaja_con_sus_vertices_en_orden() -> None:
    result = QueryResult(
        columns=("id",),
        column_types=(SqlTypeName.INT,),
        rows=((1,),),
        spatial_context=PolygonContext(table="tiendas", column="ubicacion", vertices=_VERTICES),
    )

    body = to_response(result).model_dump(mode="json")

    assert body["spatial_context"] == {
        "kind": "polygon",
        "table": "tiendas",
        "column": "ubicacion",
        "vertices": [
            {"latitude": -12.2, "longitude": -77.2},
            {"latitude": -12.2, "longitude": -76.9},
            {"latitude": -11.9, "longitude": -77.0},
        ],
    }


def test_el_radio_conserva_su_forma_de_siempre() -> None:
    result = QueryResult(
        spatial_context=RadiusContext(
            table=None,
            column="ubicacion",
            center=PointValue(-12.0464, -77.0428),
            radius=5000.0,
            metric=DistanceMetric.HAVERSINE,
            unit="meters",
            operator=ComparisonOperator.LESS_THAN_OR_EQUAL,
        ),
    )

    body = to_response(result).model_dump(mode="json")

    assert body["spatial_context"]["kind"] == "radius"
    assert body["spatial_context"]["center"] == {"latitude": -12.0464, "longitude": -77.0428}
    assert "vertices" not in body["spatial_context"]


def test_sin_condicion_espacial_no_hay_contexto() -> None:
    assert to_response(QueryResult()).model_dump(mode="json")["spatial_context"] is None
