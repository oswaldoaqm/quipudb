"""Enrutamiento (#279): destino y SQL de cada sentencia, sin red ni motor."""

import copy
from datetime import date

import pytest

from engine.distributed.catalog import DistributedCatalog
from engine.distributed.router import Execute, Scan, double_sql, literal_sql, route, scan_sql
from engine.parser import parse_sql_script
from engine.parser.ast import DateLiteral, OrderDirection
from engine.parser.errors import SQLParseError, SQLSemanticError, SQLUnsupportedError
from engine.parser.span import Span


@pytest.fixture
def catalog(catalog_data):
    """``items`` range con más tipos y ``notas`` single en n2."""

    data = copy.deepcopy(catalog_data)
    data["tables"][0]["columns"].extend(
        [
            {"name": "precio", "type": "DOUBLE", "size": None},
            {"name": "fecha", "type": "DATE", "size": None},
            {"name": "activo", "type": "BOOL", "size": None},
            {"name": "lugar", "type": "POINT", "size": None},
        ]
    )
    data["tables"].append(
        {
            "name": "notas",
            "mode": "single",
            "storage": "heap",
            "primary_key": "codigo",
            "columns": [
                {"name": "codigo", "type": "INT", "size": None},
                {"name": "texto", "type": "VARCHAR", "size": 40},
            ],
            "indexes": [],
            "fragments": [
                {"fragment_id": "notas_f0", "node_id": "n2", "lower": None, "upper": None}
            ],
        }
    )
    return DistributedCatalog.model_validate(data)


def fragment_ids(target: Scan) -> list[str]:
    return [f.fragment_id for f in target.fragments]


def unsupported(catalog, sql: str) -> SQLUnsupportedError:
    with pytest.raises(SQLUnsupportedError) as caught:
        route(catalog, sql)
    assert caught.value.message.startswith("[UNSUPPORTED_DISTRIBUTED] ")
    return caught.value


# -- escrituras ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "node", "fragment"),
    [
        (-2147483648, "n1", "items_f0"),
        (9999, "n1", "items_f0"),
        (10000, "n2", "items_f1"),
        (2147483647, "n2", "items_f1"),
    ],
)
def test_insert_va_al_dueno_de_la_clave(catalog, key, node, fragment):
    sql = f"INSERT INTO items VALUES ({key}, 'x', 1.0, DATE '2026-01-01', TRUE, POINT(0, 0))"
    assert route(catalog, sql) == Execute(node, fragment, "items", sql, write=True)


def test_delete_por_clave_va_al_dueno(catalog):
    sql = "DELETE FROM items WHERE id = 10000"
    assert route(catalog, sql) == Execute("n2", "items_f1", "items", sql, write=True)


@pytest.mark.parametrize("where", ["id > 3", "nombre = 'x'", "id BETWEEN 1 AND 3", "precio = 1.0"])
def test_delete_range_sin_igualdad_de_clave_no_se_admite(catalog, where):
    error = unsupported(catalog, f"DELETE FROM items WHERE {where}")
    assert "WHERE id = valor" in error.message
    assert error.span.column == len("DELETE FROM items WHERE ") + 1


def test_insert_invalido_da_el_error_semantico_del_motor(catalog):
    with pytest.raises(SQLSemanticError):
        route(catalog, "INSERT INTO items VALUES (1, 'x')")


# -- lecturas range --------------------------------------------------------------


def test_select_por_clave_va_a_un_solo_fragmento(catalog):
    target = route(catalog, "SELECT * FROM items WHERE id = 10000")
    assert fragment_ids(target) == ["items_f1"]
    assert target.sql == "SELECT * FROM items WHERE id = 10000;"
    assert target.projection == (0, 1, 2, 3, 4, 5)


@pytest.mark.parametrize(
    ("where", "expected"),
    [
        ("id < 10000", ["items_f0"]),
        ("id <= 10000", ["items_f0", "items_f1"]),
        ("id >= 10000", ["items_f1"]),
        ("id > 9999", ["items_f1"]),
        ("id > 9998", ["items_f0", "items_f1"]),
        ("id < -2147483648", []),
        ("id > 2147483647", []),
        ("nombre = 'x'", ["items_f0", "items_f1"]),
    ],
)
def test_comparaciones_descartan_fragmentos_por_la_clave(catalog, where, expected):
    assert fragment_ids(route(catalog, f"SELECT * FROM items WHERE {where}")) == expected


def test_select_sin_where_va_a_todos_en_orden_de_rango(catalog):
    target = route(catalog, "SELECT nombre, id FROM items ORDER BY id DESC LIMIT 3")
    assert fragment_ids(target) == ["items_f0", "items_f1"]
    assert target.sql == "SELECT * FROM items;"
    assert (target.projection, target.order, target.limit) == ((1, 0), OrderDirection.DESC, 3)


def test_columnas_calificadas_y_repetidas(catalog):
    target = route(catalog, "SELECT items.nombre, id, id FROM items ORDER BY items.id")
    assert target.projection == (1, 0, 0)
    assert target.order is OrderDirection.ASC


def test_el_scan_reescribe_cada_literal_desde_el_ast(catalog):
    casos = {
        "SELECT * FROM items WHERE nombre = 'o''k'": "nombre = 'o''k'",
        "SELECT * FROM items WHERE precio > -0.00001": "precio > -0.00001",
        "SELECT * FROM items WHERE precio > 3": "precio > 3",
        "SELECT * FROM items WHERE fecha >= DATE '2026-02-03'": "fecha >= DATE '2026-02-03'",
        "SELECT * FROM items WHERE activo = FALSE": "activo = FALSE",
    }
    for sql, where in casos.items():
        target = route(catalog, sql)
        assert target.sql == f"SELECT * FROM items WHERE {where};"
        # Lo generado vuelve a dar el mismo AST de condición.
        (original,) = parse_sql_script(sql)
        (generado,) = parse_sql_script(target.sql)
        assert generado.where.value.value == original.where.value.value


@pytest.mark.parametrize(
    ("value", "text"),
    [(1e-20, "0.00000000000000000001"), (1e20, "100000000000000000000.0"), (-2.5, "-2.5")],
)
def test_double_sin_exponente(value, text):
    assert double_sql(value) == text
    (statement,) = parse_sql_script(f"SELECT * FROM t WHERE x = {text}")
    assert statement.where.value.value == value


def test_literal_fecha_y_double_no_finito():
    span = Span(0, 0, 1, 1, 1, 1)
    assert literal_sql(DateLiteral(date(2026, 1, 9), span)) == "DATE '2026-01-09'"
    with pytest.raises(ValueError):
        double_sql(float("inf"))


def test_scan_sql_sin_where():
    assert scan_sql("items", None) == "SELECT * FROM items;"


@pytest.mark.parametrize(
    ("sql", "fragment"),
    [
        ("SELECT nombre, COUNT(*) FROM items GROUP BY nombre", "GROUP BY"),
        ("SELECT * FROM items ORDER BY nombre", "ordenar por id"),
        ("SELECT * FROM items WHERE id BETWEEN 1 AND 5", "comparación"),
        ("SELECT * FROM items WHERE DISTANCIA(lugar, POINT(0, 0)) < 5", "comparación"),
        ("SELECT * FROM items WHERE lugar = POINT(0, 0)", "POINT ni VECTOR"),
        ("SELECT * FROM items ORDER BY DISTANCIA(lugar, POINT(0, 0))", "ordenar por id"),
        ("EXPLAIN SELECT * FROM items", "EXPLAIN"),
        ("SELECT * FROM items JOIN notas ON items.id = notas.codigo", "JOIN"),
        ("SELECT * FROM items; SELECT * FROM items", "una sentencia"),
        ("DROP TABLE items", "DDL"),
        ("BEGIN TRANSACTION", "transacciones"),
    ],
)
def test_fuera_de_la_matriz_v1(catalog, sql, fragment):
    assert fragment in unsupported(catalog, sql).message


def test_la_ubicacion_del_error_apunta_a_lo_no_admitido(catalog):
    error = unsupported(catalog, "SELECT * FROM items ORDER BY nombre DESC")
    assert (error.span.line, error.span.column) == (1, 21)


def test_errores_semanticos_antes_que_no_admitidos(catalog):
    with pytest.raises(SQLSemanticError) as caught:
        route(catalog, "SELECT foo FROM items ORDER BY nombre")
    assert "foo" in caught.value.message


def test_tabla_inexistente_con_el_texto_del_motor(catalog):
    with pytest.raises(SQLSemanticError) as caught:
        route(catalog, "SELECT * FROM otra")
    assert caught.value.message == "la tabla 'otra' no existe"
    assert caught.value.span.column == 15


def test_error_de_sintaxis_del_parser(catalog):
    with pytest.raises(SQLParseError):
        route(catalog, "SELECT FROM items")


# -- tablas single ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("sql", "write"),
    [
        ("SELECT texto FROM notas ORDER BY texto LIMIT 1", False),
        ("SELECT texto, COUNT(*) FROM notas GROUP BY texto", False),
        ("EXPLAIN SELECT * FROM notas WHERE codigo = 1", False),
        ("INSERT INTO notas VALUES (1, 'a')", True),
        ("DELETE FROM notas WHERE texto = 'a'", True),
    ],
)
def test_single_manda_el_sql_original_a_su_dueno(catalog, sql, write):
    assert route(catalog, sql) == Execute("n2", "notas_f0", "notas", sql, write=write)
