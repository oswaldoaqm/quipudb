"""Las traducciones que el frontend no deberia hacer, probadas sin HTTP."""

from __future__ import annotations

import pytest

from engine.api.service import describe_table, to_error, to_response
from engine.executor.result import QueryResult
from engine.parser.ast import SqlTypeName
from engine.parser.bound_ast import BoundColumn, BoundSchema
from engine.parser.errors import (
    SQLLexError,
    SQLParseError,
    SQLSemanticError,
    SQLUnsupportedError,
)
from engine.parser.parser import parse_sql
from engine.parser.span import Span
from engine.planner.optimizer import IndexMetadata, TableMetadata
from engine.planner.plan import Structure

_ESQUEMA = BoundSchema(
    "alumnos",
    (
        BoundColumn("codigo", SqlTypeName.INT, None),
        BoundColumn("nombre", SqlTypeName.VARCHAR, 32),
        BoundColumn("promedio", SqlTypeName.DOUBLE, None),
    ),
    0,
)


def _metadata(*indexes: IndexMetadata, rows: int | None = 10_000) -> TableMetadata:
    return TableMetadata("alumnos", Structure.HEAP, tuple(indexes), rows=rows)


def test_el_indice_viaja_con_el_nombre_de_su_columna_no_su_posicion() -> None:
    indice = IndexMetadata("por_promedio", 2, Structure.BPLUS_UNCLUSTERED, supports_range=True)

    info = describe_table(_ESQUEMA, _metadata(indice))

    assert info.indexes[0].column == "promedio"
    assert info.indexes[0].structure == "bplus_unclustered"
    assert info.indexes[0].supports_range is True


def test_la_clave_primaria_pasa_de_posicion_a_booleano_por_columna() -> None:
    info = describe_table(_ESQUEMA, _metadata())

    assert [columna.is_primary_key for columna in info.columns] == [True, False, False]


def test_solo_varchar_declara_tamano() -> None:
    info = describe_table(_ESQUEMA, _metadata())

    assert [columna.size for columna in info.columns] == [None, 32, None]


def test_el_conteo_de_filas_llega_como_record_count() -> None:
    assert describe_table(_ESQUEMA, _metadata()).record_count == 10_000


def test_sin_conteo_conocido_record_count_es_cero_y_no_none() -> None:
    assert describe_table(_ESQUEMA, _metadata(rows=None)).record_count == 0


def test_la_organizacion_de_la_tabla_viaja_como_storage() -> None:
    assert describe_table(_ESQUEMA, _metadata()).storage == "heap"


def test_un_indice_que_apunta_a_una_columna_inexistente_se_denuncia() -> None:
    roto = IndexMetadata("roto", 9, Structure.EXTENDIBLE_HASH, supports_range=False)

    with pytest.raises(ValueError, match="columna inexistente 9"):
        describe_table(_ESQUEMA, _metadata(roto))


# ---------------------------------------------------------------------------
# Errores
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("clase", "esperado"),
    [
        (SQLLexError, "lex"),
        (SQLParseError, "parse"),
        (SQLSemanticError, "semantic"),
        (SQLUnsupportedError, "unsupported"),
    ],
)
def test_kind_sale_de_la_clase_de_la_excepcion(clase, esperado) -> None:
    error = clase("cualquier cosa", Span(0, 4, 1, 1, 1, 5), "SELECT")

    assert to_error(error).kind == esperado


def test_la_ubicacion_se_cuenta_desde_uno_y_el_final_es_exclusivo() -> None:
    sql = "SELECT * FROM alumnos WHERE nombre LIKE 'a%'"

    try:
        parse_sql(sql)
    except SQLParseError as error:
        cuerpo = to_error(error)
    else:  # pragma: no cover
        pytest.fail("la consulta deberia fallar")

    assert cuerpo.line == 1
    assert cuerpo.end_line == 1
    assert sql[cuerpo.column - 1 : cuerpo.end_column - 1] == "LIKE"


def test_el_mensaje_del_motor_viaja_sin_reformular() -> None:
    error = SQLUnsupportedError(
        "LIKE no esta soportado por el subconjunto SQL de QuipuDB",
        Span(35, 39, 1, 36, 1, 40),
        "SELECT",
    )

    assert to_error(error).error == "LIKE no esta soportado por el subconjunto SQL de QuipuDB"


# ---------------------------------------------------------------------------
# Resultados
# ---------------------------------------------------------------------------


def test_las_filas_viajan_como_listas_en_el_orden_de_columns() -> None:
    resultado = QueryResult(
        columns=("nombre", "promedio"),
        column_types=(SqlTypeName.VARCHAR, SqlTypeName.DOUBLE),
        rows=(("ana", 15.6), ("bruno", 16.2)),
    )

    cuerpo = to_response(resultado)

    assert cuerpo.columns == ["nombre", "promedio"]
    assert cuerpo.column_types == [SqlTypeName.VARCHAR, SqlTypeName.DOUBLE]
    assert cuerpo.rows == [["ana", 15.6], ["bruno", 16.2]]
    assert cuerpo.plan is None


def test_una_sentencia_sin_filas_informa_en_affected_rows() -> None:
    cuerpo = to_response(QueryResult(affected_rows=3))

    assert cuerpo.columns == []
    assert cuerpo.column_types == []
    assert cuerpo.rows == []
    assert cuerpo.affected_rows == 3
    assert cuerpo.plan is None
