"""Pruebas del resultado publico de una consulta."""

from dataclasses import FrozenInstanceError

import pytest

from engine.executor.result import QueryResult
from engine.planner.plan import Op, Plan, Step, Structure


def test_defaults_represent_command_without_returned_rows() -> None:
    result = QueryResult()

    assert result.columns == ()
    assert result.rows == ()
    assert result.affected_rows == 0
    assert result.plan is None

    command_result = QueryResult(affected_rows=2)
    assert command_result.columns == ()
    assert command_result.rows == ()
    assert command_result.affected_rows == 2


def test_select_result_preserves_columns_and_rows() -> None:
    result = QueryResult(
        columns=("id", "name"),
        rows=((1, "Ada"), (2, "Grace")),
    )

    assert result.columns == ("id", "name")
    assert result.rows == ((1, "Ada"), (2, "Grace"))
    assert result.affected_rows == 0


def test_preserves_execution_plan() -> None:
    plan = Plan(
        query="SELECT id FROM users",
        root=Step(op=Op.SCAN, structure=Structure.HEAP, table="users"),
    )

    result = QueryResult(columns=("id",), rows=((1,),), plan=plan)

    assert result.plan is plan


def test_result_is_immutable() -> None:
    result = QueryResult(columns=("id",), rows=((1,),))

    with pytest.raises(FrozenInstanceError):
        result.affected_rows = 1  # type: ignore[misc]

    with pytest.raises(TypeError):
        result.columns[0] = "other"  # type: ignore[index]

    with pytest.raises(TypeError):
        result.rows[0][0] = 2  # type: ignore[index]


def test_copies_mutable_collections_to_immutable_tuples() -> None:
    columns = ["id"]
    rows = [[1]]

    result = QueryResult(columns=columns, rows=rows)  # type: ignore[arg-type]
    columns[0] = "changed"
    rows[0][0] = 2

    assert result.columns == ("id",)
    assert result.rows == ((1,),)


def test_rejects_negative_affected_rows() -> None:
    with pytest.raises(ValueError, match="affected_rows no puede ser negativo"):
        QueryResult(affected_rows=-1)


@pytest.mark.parametrize(
    ("columns", "rows", "message"),
    [
        (("id",), ((1, "extra"),), "la fila 0 tiene 2 valores; se esperaban 1"),
        (("id", "name"), ((1, "Ada"), (2,)), "la fila 1 tiene 1 valores; se esperaban 2"),
        ((), ((1,),), "la fila 0 tiene 1 valores; se esperaban 0"),
    ],
)
def test_rejects_rows_whose_width_differs_from_columns(
    columns: tuple[str, ...],
    rows: tuple[tuple[object, ...], ...],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        QueryResult(columns=columns, rows=rows)
