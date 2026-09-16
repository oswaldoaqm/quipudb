"""Prueba de humo de la simulacion de hilos (#32): que no se rompa con el tiempo.

No repite las pruebas de `engine/test_concurrency.py` (que ya cubren el
lock manager con hilos reales a fondo); solo confirma que `run_mode` sigue
corriendo sin excepciones en cada modo, con parametros chicos para que sea
rapida.
"""

from __future__ import annotations

import pytest

from engine.transactions.demo_concurrencia import run_mode

quipudb = pytest.importorskip(
    "quipudb_native",
    reason="los bindings no estan compilados: cmake -DQUIPUDB_BUILD_PYTHON=ON",
)


def test_modo_sin_lock_corre_y_reporta_incorrecto() -> None:
    correcto = run_mode("sin-lock", hilos=3, incrementos_por_hilo=2, delay=0.01)

    assert correcto is False


def test_modo_con_lock_corre_y_reporta_correcto() -> None:
    correcto = run_mode("con-lock", hilos=2, incrementos_por_hilo=2, delay=0.01)

    assert correcto is True
