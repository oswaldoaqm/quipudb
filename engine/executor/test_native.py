"""Pruebas de conversion en la frontera entre SQL y pybind11."""

from dataclasses import dataclass
from datetime import date
from math import isinf

import pytest

from engine.executor.native import (
    from_native_record,
    native_range_bounds,
    to_native_value,
)
from engine.parser.ast import SqlTypeName
from engine.parser.bound_ast import BoundColumn, BoundSchema


@dataclass(frozen=True)
class _Date:
    days: int


class _Native:
    Date = _Date


def _column(data_type: SqlTypeName, length: int | None = None) -> BoundColumn:
    return BoundColumn("valor", data_type, length)


def test_convierte_date_en_ambas_direcciones() -> None:
    value = date(2026, 9, 13)
    native_value = to_native_value(value, _Native)
    schema = BoundSchema("eventos", (_column(SqlTypeName.DATE),), 0)

    assert native_value == _Date((value - date(1970, 1, 1)).days)
    assert from_native_record([native_value], schema) == (value,)


def test_copia_los_otros_tipos_sin_confundir_bool_con_int() -> None:
    schema = BoundSchema(
        "datos",
        (
            _column(SqlTypeName.INT),
            _column(SqlTypeName.DOUBLE),
            _column(SqlTypeName.VARCHAR, 8),
            _column(SqlTypeName.BOOL),
        ),
        0,
    )

    row = from_native_record([7, 1.5, "Ada", True], schema)

    assert row == (7, 1.5, "Ada", True)
    assert type(row[0]) is int
    assert type(row[3]) is bool


def test_rechaza_registro_nativo_con_ancho_incoherente() -> None:
    schema = BoundSchema("datos", (_column(SqlTypeName.INT),), 0)

    with pytest.raises(RuntimeError, match="2 columnas"):
        from_native_record([1, 2], schema)


def test_rechaza_fecha_nativa_invalida_con_contexto_de_columna() -> None:
    schema = BoundSchema("datos", (_column(SqlTypeName.DATE),), 0)

    with pytest.raises(RuntimeError, match="'valor'"):
        from_native_record([object()], schema)


@pytest.mark.parametrize(
    ("data_type", "lower", "upper"),
    [
        (SqlTypeName.INT, -(2**31), 2**31 - 1),
        (SqlTypeName.BOOL, False, True),
    ],
)
def test_limites_nativos_de_dominios_discretos(
    data_type: SqlTypeName,
    lower: object,
    upper: object,
) -> None:
    assert native_range_bounds(_column(data_type), _Native) == (lower, upper)


def test_limites_double_cubren_todo_valor_finito() -> None:
    lower, upper = native_range_bounds(_column(SqlTypeName.DOUBLE), _Native)

    assert isinf(lower) and lower < 0
    assert isinf(upper) and upper > 0


@pytest.mark.parametrize("length", [1, 2, 3, 4, 5, 8, 9])
def test_limite_varchar_es_utf8_valido_y_ocupa_su_capacidad(length: int) -> None:
    lower, upper = native_range_bounds(_column(SqlTypeName.VARCHAR, length), _Native)

    assert lower == ""
    assert isinstance(upper, str)
    assert len(upper.encode("utf-8")) == length


def test_limites_date_usan_el_tipo_nativo_y_evitan_overflow() -> None:
    lower, upper = native_range_bounds(_column(SqlTypeName.DATE), _Native)

    assert lower == _Date(-(2**31))
    assert upper == _Date(2**31 - 1)
