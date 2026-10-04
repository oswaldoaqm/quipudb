"""``contains_point`` en Python: debe decidir igual que el del core."""

import random

import pytest

from engine.executor.predicates import contains_point
from engine.parser.bound_ast import PointValue

# Forma de L, no convexa, con x = longitud e y = latitud como en el core.
_L = tuple(
    PointValue(lat, lon)
    for lat, lon in [
        (-12.20, -77.20),
        (-12.20, -76.90),
        (-12.10, -76.90),
        (-12.10, -77.05),
        (-11.90, -77.05),
        (-11.90, -77.20),
    ]
)


@pytest.mark.parametrize(
    ("latitud", "longitud", "esperado"),
    [
        (-12.15, -77.10, True),  # dentro del brazo horizontal
        (-12.00, -77.10, True),  # dentro del brazo vertical
        (-12.00, -76.95, False),  # en el hueco: dentro de la caja, fuera de la L
        (-12.30, -77.10, False),  # fuera de la caja
        (-12.20, -77.00, True),  # sobre una arista
        (-12.10, -77.05, True),  # sobre el vertice concavo
        (-11.90, -77.20, True),  # sobre un vertice convexo
    ],
)
def test_contains_point_casos_conocidos(latitud, longitud, esperado) -> None:
    assert contains_point(_L, PointValue(latitud, longitud)) is esperado


def test_contains_point_rechaza_menos_de_tres_vertices() -> None:
    with pytest.raises(ValueError, match="al menos 3 vertices"):
        contains_point(_L[:2], PointValue(0.0, 0.0))


def test_contains_point_coincide_con_el_core() -> None:
    native = pytest.importorskip(
        "quipudb_native",
        reason="los bindings no estan compilados: cmake -DQUIPUDB_BUILD_PYTHON=ON",
    )
    vertices = [native.GeoPoint(v.latitude, v.longitude) for v in _L]
    generador = random.Random(20260906)
    for _ in range(5_000):
        if generador.random() < 0.3:
            # Puntos sobre las aristas: es donde una traduccion descuidada difiere.
            a, b = generador.sample(list(_L), 2)
            t = generador.random()
            punto = PointValue(
                a.latitude + t * (b.latitude - a.latitude),
                a.longitude + t * (b.longitude - a.longitude),
            )
        else:
            punto = PointValue(generador.uniform(-12.3, -11.8), generador.uniform(-77.3, -76.8))
        esperado = native.contains_point(
            vertices, native.GeoPoint(punto.latitude, punto.longitude)
        )
        assert contains_point(_L, punto) is esperado, punto
