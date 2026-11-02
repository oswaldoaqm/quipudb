"""Conversion de una columna VECTOR en la frontera con pybind11 (#180).

Estas pruebas existen porque el tipo quedo a medias la primera vez: el
parser lo aceptaba, el core lo guardaba, y `CREATE TABLE ... VECTOR(3)` moria
con un `KeyError` en el mapeo de tipos del ejecutor. No lo vio ninguna prueba
del parser ni del codec, porque el mapeo esta justo entre los dos.

No necesitan los bindings: se usa un doble del modulo nativo, igual que
`test_native.py`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import pytest

from engine.executor.native import (
    from_native_record,
    from_native_schema,
    to_native_schema,
    to_native_value,
)
from engine.parser.ast import SqlTypeName
from engine.parser.bound_ast import BoundColumn, BoundSchema


class _DataType(Enum):
    INT = 0
    DOUBLE = 1
    VARCHAR = 2
    BOOL = 3
    DATE = 4
    POINT = 5
    VECTOR = 6


@dataclass(frozen=True)
class _Column:
    name: str
    type: _DataType
    length: int = 0


@dataclass(frozen=True)
class _Schema:
    table_name: str
    columns: list
    key_column: int


class _Native:
    DataType = _DataType
    Column = _Column
    Schema = _Schema


FOTOS = BoundSchema(
    "fotos",
    (
        BoundColumn("id", SqlTypeName.INT, None),
        BoundColumn("rasgos", SqlTypeName.VECTOR, 3),
    ),
    0,
)


# ---------------------------------------------------------------------------
# El esquema cruza en las dos direcciones
# ---------------------------------------------------------------------------


def test_el_tipo_vector_se_traduce_al_core() -> None:
    # Esto es lo que fallaba con KeyError.
    nativo = to_native_schema(FOTOS, _Native)
    assert nativo.columns[1].type is _DataType.VECTOR


def test_la_dimension_viaja_al_core() -> None:
    assert to_native_schema(FOTOS, _Native).columns[1].length == 3


def test_el_tipo_vector_vuelve_del_core() -> None:
    nativo = to_native_schema(FOTOS, _Native)
    assert from_native_schema(nativo, _Native).columns[1].data_type is SqlTypeName.VECTOR


def test_la_dimension_vuelve_del_core() -> None:
    # Sin esto, una tabla reabierta volveria con VECTOR(None) y `byte_size`
    # no sabria cuanto ocupa la columna.
    nativo = to_native_schema(FOTOS, _Native)
    assert from_native_schema(nativo, _Native).columns[1].length == 3


def test_el_esquema_sobrevive_a_la_ida_y_vuelta() -> None:
    assert from_native_schema(to_native_schema(FOTOS, _Native), _Native) == FOTOS


def test_los_tipos_sin_dimension_siguen_volviendo_sin_longitud() -> None:
    nativo = to_native_schema(FOTOS, _Native)
    assert from_native_schema(nativo, _Native).columns[0].length is None


# ---------------------------------------------------------------------------
# Los valores
# ---------------------------------------------------------------------------


def test_un_vector_viaja_al_core_tal_cual() -> None:
    # El caster de pybind11 acepta list y tuple; no hay nada que convertir.
    assert to_native_value((1.0, 2.0, 3.0), _Native) == (1.0, 2.0, 3.0)


def test_un_vector_vuelve_como_tupla() -> None:
    # El core devuelve una lista, pero el modelo semantico usa tuplas, que es
    # lo que produce `_bind_vector` al insertar. Si no coincidieran, comparar
    # lo insertado con lo leido fallaria por el tipo.
    fila = from_native_record([1, [0.5, -1.25, 2.0]], FOTOS)
    assert fila[1] == (0.5, -1.25, 2.0)
    assert isinstance(fila[1], tuple)


def test_las_componentes_enteras_vuelven_como_numeros() -> None:
    assert from_native_record([1, [1, 2, 3]], FOTOS)[1] == (1.0, 2.0, 3.0)


def test_un_vector_vacio_del_core_vuelve_vacio() -> None:
    # No es un caso valido del esquema, pero la conversion no debe inventar.
    esquema = BoundSchema("t", (BoundColumn("v", SqlTypeName.VECTOR, 0),), 0)
    assert from_native_record([[]], esquema) == ((),)


def test_un_vector_ilegible_del_core_se_denuncia() -> None:
    with pytest.raises(RuntimeError, match="VECTOR invalido"):
        from_native_record([1, ["a", "b", "c"]], FOTOS)


def test_un_valor_que_no_es_secuencia_se_denuncia() -> None:
    with pytest.raises(RuntimeError, match="VECTOR invalido"):
        from_native_record([1, 3.5], FOTOS)
