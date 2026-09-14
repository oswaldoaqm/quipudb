"""Pruebas puras de seleccion de rutas para ``SELECT``."""

from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest

from engine.parser.ast import ComparisonOperator, SqlTypeName
from engine.parser.bound_ast import (
    BoundBetweenCondition,
    BoundColumn,
    BoundColumnReference,
    BoundComparisonCondition,
    BoundSchema,
    BoundSelectStatement,
)
from engine.parser.span import Span
from engine.planner.native_catalog import from_native_table_info
from engine.planner.optimizer import (
    AccessRoute,
    IndexMetadata,
    PhysicalSelectPlan,
    TableMetadata,
    optimize_select,
)
from engine.planner.plan import Structure

_SPAN = Span(0, 1, 1, 1, 1, 2)
_SCHEMA = BoundSchema(
    "alumnos",
    (
        BoundColumn("codigo", SqlTypeName.INT, None),
        BoundColumn("promedio", SqlTypeName.DOUBLE, None),
        BoundColumn("nombre", SqlTypeName.VARCHAR, 40),
    ),
    key_column=0,
)


def _column(index: int) -> BoundColumnReference:
    return BoundColumnReference(index, _SCHEMA.columns[index], _SPAN)


def _comparison(
    index: int,
    operator: ComparisonOperator,
) -> BoundComparisonCondition:
    value: int | float | str = (7, 15.0, "Ana")[index]
    return BoundComparisonCondition(_column(index), operator, value, _SPAN)


def _between(index: int = 1) -> BoundBetweenCondition:
    return BoundBetweenCondition(_column(index), 14.0, 18.0, _SPAN)


def _select(where=None) -> BoundSelectStatement:
    return BoundSelectStatement(
        schema=_SCHEMA,
        projections=tuple(_column(index) for index in range(len(_SCHEMA.columns))),
        wildcard=True,
        where=where,
        span=_SPAN,
    )


def _index(
    name: str,
    structure: Structure,
    column: int = 1,
) -> IndexMetadata:
    return IndexMetadata(
        name=name,
        column=column,
        structure=structure,
        supports_range=structure is Structure.BPLUS_UNCLUSTERED,
    )


def _table(*indexes: IndexMetadata) -> TableMetadata:
    return TableMetadata("alumnos", Structure.HEAP, indexes)


def test_select_sin_where_hace_scan_sin_filtro_residual() -> None:
    plan = optimize_select(_select(), _table())

    assert plan.route is AccessRoute.SCAN
    assert plan.index is None
    assert plan.residual_filter is False


@pytest.mark.parametrize(
    ("condition", "route", "residual"),
    [
        (_comparison(0, ComparisonOperator.EQUAL), AccessRoute.TABLE_SEARCH, False),
        (_between(0), AccessRoute.TABLE_RANGE, False),
        (_comparison(0, ComparisonOperator.LESS_THAN), AccessRoute.TABLE_RANGE, True),
        (
            _comparison(0, ComparisonOperator.GREATER_THAN_OR_EQUAL),
            AccessRoute.TABLE_RANGE,
            False,
        ),
    ],
)
def test_clave_primaria_tiene_prioridad(
    condition,
    route: AccessRoute,
    residual: bool,
) -> None:
    table = _table(
        _index("hash_pk", Structure.EXTENDIBLE_HASH, column=0),
        _index("bplus_pk", Structure.BPLUS_UNCLUSTERED, column=0),
    )

    plan = optimize_select(_select(condition), table)

    assert plan.route is route
    assert plan.index is None
    assert plan.residual_filter is residual


@pytest.mark.parametrize(
    "indexes",
    [
        (
            _index("z_bplus", Structure.BPLUS_UNCLUSTERED),
            _index("z_hash", Structure.EXTENDIBLE_HASH),
        ),
        (
            _index("z_hash", Structure.EXTENDIBLE_HASH),
            _index("z_bplus", Structure.BPLUS_UNCLUSTERED),
        ),
    ],
)
def test_igualdad_prefiere_hash_sin_depender_del_orden(
    indexes: tuple[IndexMetadata, ...],
) -> None:
    plan = optimize_select(
        _select(_comparison(1, ComparisonOperator.EQUAL)),
        _table(*indexes),
    )

    assert plan.route is AccessRoute.INDEX_SEARCH
    assert plan.index is not None
    assert plan.index.name == "z_hash"
    assert plan.index.structure is Structure.EXTENDIBLE_HASH
    assert plan.residual_filter is False


def test_indices_equivalentes_se_desempatan_por_nombre() -> None:
    plan = optimize_select(
        _select(_comparison(1, ComparisonOperator.EQUAL)),
        _table(
            _index("por_promedio_z", Structure.EXTENDIBLE_HASH),
            _index("por_promedio_a", Structure.EXTENDIBLE_HASH),
        ),
    )

    assert plan.index is not None
    assert plan.index.name == "por_promedio_a"


@pytest.mark.parametrize(
    ("condition", "residual"),
    [
        (_between(), False),
        (_comparison(1, ComparisonOperator.LESS_THAN), True),
        (_comparison(1, ComparisonOperator.LESS_THAN_OR_EQUAL), False),
        (_comparison(1, ComparisonOperator.GREATER_THAN), True),
        (_comparison(1, ComparisonOperator.GREATER_THAN_OR_EQUAL), False),
    ],
)
def test_rango_omite_hash_y_elige_bplus(
    condition,
    residual: bool,
) -> None:
    plan = optimize_select(
        _select(condition),
        _table(
            _index("a_hash", Structure.EXTENDIBLE_HASH),
            _index("z_bplus", Structure.BPLUS_UNCLUSTERED),
        ),
    )

    assert plan.route is AccessRoute.INDEX_RANGE
    assert plan.index is not None
    assert plan.index.name == "z_bplus"
    assert plan.residual_filter is residual


@pytest.mark.parametrize("condition", [_between(), _comparison(1, ComparisonOperator.LESS_THAN)])
def test_hash_nunca_se_elige_para_rango(condition) -> None:
    plan = optimize_select(
        _select(condition),
        _table(_index("solo_hash", Structure.EXTENDIBLE_HASH)),
    )

    assert plan.route is AccessRoute.SCAN
    assert plan.index is None
    assert plan.residual_filter is True


def test_igualdad_sin_indice_hace_scan_y_filter() -> None:
    plan = optimize_select(
        _select(_comparison(2, ComparisonOperator.EQUAL)),
        _table(_index("otra_columna", Structure.EXTENDIBLE_HASH)),
    )

    assert plan.route is AccessRoute.SCAN
    assert plan.residual_filter is True


@pytest.mark.parametrize("value", ["x" * 41, "a\0b"])
def test_hash_no_recibe_clave_varchar_que_su_codec_no_representa(value: str) -> None:
    condition = BoundComparisonCondition(_column(2), ComparisonOperator.EQUAL, value, _SPAN)
    hash_index = _index("por_nombre_hash", Structure.EXTENDIBLE_HASH, column=2)
    bplus_index = _index("por_nombre_bplus", Structure.BPLUS_UNCLUSTERED, column=2)

    with_bplus = optimize_select(_select(condition), _table(hash_index, bplus_index))
    only_hash = optimize_select(_select(condition), _table(hash_index))

    assert with_bplus.route is AccessRoute.INDEX_SEARCH
    assert with_bplus.index == bplus_index
    assert only_hash.route is AccessRoute.SCAN
    assert only_hash.residual_filter is True


def test_ir_fisico_y_metadata_son_inmutables() -> None:
    plan = optimize_select(_select(), _table())

    with pytest.raises(FrozenInstanceError):
        plan.route = AccessRoute.TABLE_SEARCH  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        plan.table.name = "otra"  # type: ignore[misc]


def test_ir_rechaza_indice_en_una_ruta_de_tabla() -> None:
    statement = _select()
    table = _table()

    with pytest.raises(ValueError, match="rutas de indice"):
        PhysicalSelectPlan(
            statement,
            table,
            AccessRoute.SCAN,
            index=_index("sobrante", Structure.EXTENDIBLE_HASH),
        )


def test_ir_rechaza_hash_en_una_ruta_de_rango() -> None:
    statement = _select(_between())
    index = _index("solo_hash", Structure.EXTENDIBLE_HASH)
    table = _table(index)

    with pytest.raises(ValueError, match="soporte rangos"):
        PhysicalSelectPlan(statement, table, AccessRoute.INDEX_RANGE, index=index)


def test_ir_rechaza_indice_ajeno_o_de_otra_columna() -> None:
    statement = _select(_comparison(1, ComparisonOperator.EQUAL))
    listed = _index("listado", Structure.EXTENDIBLE_HASH)

    with pytest.raises(ValueError, match="no pertenece"):
        PhysicalSelectPlan(
            statement,
            _table(listed),
            AccessRoute.INDEX_SEARCH,
            index=_index("ajeno", Structure.EXTENDIBLE_HASH),
        )

    wrong_column = _index("por_nombre", Structure.EXTENDIBLE_HASH, column=2)
    with pytest.raises(ValueError, match="columna del predicado"):
        PhysicalSelectPlan(
            statement,
            _table(wrong_column),
            AccessRoute.INDEX_SEARCH,
            index=wrong_column,
        )


def test_adaptador_copia_metadata_y_deriva_capacidad_por_kind() -> None:
    table_info = SimpleNamespace(
        schema=SimpleNamespace(
            table_name="alumnos",
            columns=[SimpleNamespace(), SimpleNamespace(), SimpleNamespace()],
        ),
        storage="heap",
        indexes=[
            SimpleNamespace(
                name="por_promedio_bplus",
                column=1,
                kind="bplus_unclustered",
            ),
            SimpleNamespace(
                name="por_promedio_hash",
                column=1,
                kind="extendible_hash",
            ),
        ],
    )

    metadata = from_native_table_info(table_info)

    assert metadata == _table(
        _index("por_promedio_bplus", Structure.BPLUS_UNCLUSTERED),
        _index("por_promedio_hash", Structure.EXTENDIBLE_HASH),
    )
    assert metadata.indexes[0].supports_range is True
    assert metadata.indexes[1].supports_range is False


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("storage", "extendible_hash", "no es una estructura de tabla"),
        ("index_kind", "heap", "no es una estructura de indice"),
        ("index_column", 9, "columna inexistente"),
    ],
)
def test_adaptador_rechaza_metadata_incoherente(field: str, value, message: str) -> None:
    storage = value if field == "storage" else "heap"
    index_kind = value if field == "index_kind" else "bplus_unclustered"
    index_column = value if field == "index_column" else 1
    table_info = SimpleNamespace(
        schema=SimpleNamespace(table_name="alumnos", columns=[SimpleNamespace()] * 3),
        storage=storage,
        indexes=[SimpleNamespace(name="ix", column=index_column, kind=index_kind)],
    )

    with pytest.raises(ValueError, match=message):
        from_native_table_info(table_info)
