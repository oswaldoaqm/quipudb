"""Ruta de MATCH en el optimizador (#168)."""

import pytest

from engine.parser import SelectStatement, parse_sql
from engine.parser.ast import SqlTypeName
from engine.parser.bound_ast import BoundColumn, BoundSchema, BoundSelectStatement
from engine.parser.semantic import bind_select
from engine.planner.explain import explain_select
from engine.planner.optimizer import (
    AccessRoute,
    IndexMetadata,
    PhysicalTableAccess,
    TableMetadata,
    optimize_select,
)
from engine.planner.plan import Op, Structure

_SCHEMA = BoundSchema(
    "documentos",
    (
        BoundColumn("id", SqlTypeName.INT, None),
        BoundColumn("contenido", SqlTypeName.VARCHAR, 200),
        BoundColumn("autor", SqlTypeName.VARCHAR, 40),
    ),
    0,
)
_TEXTO = IndexMetadata("por_contenido", 1, Structure.INVERTED_INDEX, supports_range=False)


def _bind(source: str) -> BoundSelectStatement:
    statement = parse_sql(source)
    assert isinstance(statement, SelectStatement)
    return bind_select(statement, _SCHEMA, source)


def _table(*indexes: IndexMetadata) -> TableMetadata:
    return TableMetadata("documentos", Structure.HEAP, indexes)


def test_match_usa_el_indice_de_texto_sin_filtro_residual() -> None:
    plan = optimize_select(
        _bind("SELECT * FROM documentos WHERE MATCH(contenido, 'oro')"), _table(_TEXTO)
    )

    assert plan.route is AccessRoute.TEXT_SEARCH
    assert plan.index == _TEXTO
    assert plan.residual_filter is False
    assert plan.access.text_limit is None  # sin LIMIT: todos los que coinciden


def test_el_limit_sin_order_by_baja_al_indice() -> None:
    plan = optimize_select(
        _bind("SELECT * FROM documentos WHERE MATCH(contenido, 'oro') USING TF_IDF LIMIT 10"),
        _table(_TEXTO),
    )

    assert plan.route is AccessRoute.TEXT_SEARCH
    assert plan.access.text_limit == 10


@pytest.mark.parametrize(
    "source",
    [
        # Con ORDER BY hay que traer todos y ordenar antes de cortar.
        "SELECT * FROM documentos WHERE MATCH(contenido, 'oro') ORDER BY autor LIMIT 5",
        # Con GROUP BY el LIMIT corta grupos, no documentos.
        (
            "SELECT autor, COUNT(*) FROM documentos WHERE MATCH(contenido, 'oro') "
            "GROUP BY autor LIMIT 5"
        ),
    ],
)
def test_con_order_by_o_group_by_el_limit_no_baja(source: str) -> None:
    plan = optimize_select(_bind(source), _table(_TEXTO))

    assert plan.route is AccessRoute.TEXT_SEARCH
    assert plan.access.text_limit is None


def test_sin_indice_de_texto_no_hay_ruta() -> None:
    hash_ = IndexMetadata("por_contenido_hash", 1, Structure.EXTENDIBLE_HASH, supports_range=False)
    statement = _bind("SELECT * FROM documentos WHERE MATCH(contenido, 'oro')")

    for table in (_table(), _table(hash_)):
        with pytest.raises(ValueError, match="indice de texto"):
            optimize_select(statement, table)


def test_un_indice_de_texto_en_otra_columna_no_sirve() -> None:
    en_autor = IndexMetadata("por_autor", 2, Structure.INVERTED_INDEX, supports_range=False)

    with pytest.raises(ValueError, match="indice de texto"):
        optimize_select(
            _bind("SELECT * FROM documentos WHERE MATCH(contenido, 'oro')"), _table(en_autor)
        )


def test_dos_indices_de_texto_se_desempatan_por_nombre() -> None:
    a = IndexMetadata("a_texto", 1, Structure.INVERTED_INDEX, supports_range=False)
    z = IndexMetadata("z_texto", 1, Structure.INVERTED_INDEX, supports_range=False)

    plan = optimize_select(
        _bind("SELECT * FROM documentos WHERE MATCH(contenido, 'oro')"), _table(z, a)
    )

    assert plan.index == a


def test_el_ir_protege_la_ruta_de_texto() -> None:
    hash_ = IndexMetadata("h", 1, Structure.EXTENDIBLE_HASH, supports_range=False)
    with pytest.raises(ValueError, match="necesita un indice de texto"):
        PhysicalTableAccess(_table(hash_), AccessRoute.TEXT_SEARCH, hash_)
    with pytest.raises(ValueError, match="filtro residual"):
        PhysicalTableAccess(_table(_TEXTO), AccessRoute.TEXT_SEARCH, _TEXTO, residual_filter=True)
    with pytest.raises(ValueError, match="solo TEXT_SEARCH"):
        PhysicalTableAccess(_table(), AccessRoute.SCAN, text_limit=5)
    with pytest.raises(ValueError, match="negativo"):
        PhysicalTableAccess(_table(_TEXTO), AccessRoute.TEXT_SEARCH, _TEXTO, text_limit=-1)


def test_explain_muestra_la_busqueda_de_texto_y_el_fetch() -> None:
    source = "SELECT * FROM documentos WHERE MATCH(contenido, 'oro') USING TF_IDF LIMIT 3"
    plan = explain_select(optimize_select(_bind(source), _table(_TEXTO)), source, source)

    limit = plan.root
    assert limit.op is Op.LIMIT
    fetch = limit.children[0]
    assert fetch.op is Op.FETCH
    texto = fetch.children[0]
    assert texto.op is Op.TEXT_SEARCH
    assert texto.structure is Structure.INVERTED_INDEX
    assert texto.column == "contenido"
    assert "MATCH(contenido, 'oro') USING TF_IDF" in texto.detail
