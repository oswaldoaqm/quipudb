"""DDL distribuido (#296): catálogo resultante, errores con posición y cambios por nodo."""

import pytest

from engine.distributed.ddl import (
    CatalogChangeError,
    apply_ddl,
    changes_for_node,
    create_index_sql,
    create_table_sql,
)
from engine.parser import parse_sql
from engine.parser.errors import SQLSemanticError, SQLUnsupportedError

VENTAS = """CREATE TABLE ventas (id INT PRIMARY KEY, monto DOUBLE, cliente VARCHAR(20))
PARTITION BY RANGE (id) (
  PARTITION ventas_f0 VALUES LESS THAN (5000) ON n1,
  PARTITION ventas_f1 VALUES LESS THAN (MAXVALUE) ON n2
)"""


def ddl(catalog, sql):
    return apply_ddl(catalog, parse_sql(sql), sql)


def failure(catalog, sql, error=SQLSemanticError):
    with pytest.raises(error) as caught:
        ddl(catalog, sql)
    span = caught.value.span
    return caught.value.message, sql[span.start : span.end]


def test_create_table_particionada_agrega_la_tabla_y_sube_la_version(node_catalog):
    catalog = ddl(node_catalog, VENTAS)

    assert catalog.catalog_version == node_catalog.catalog_version + 1
    assert catalog.nodes == node_catalog.nodes
    assert catalog.table("items") == node_catalog.table("items")
    ventas = catalog.table("ventas")
    assert (ventas.mode, ventas.storage, ventas.primary_key) == ("range", "heap", "id")
    assert [(c.name, c.type, c.size) for c in ventas.columns] == [
        ("id", "INT", None),
        ("monto", "DOUBLE", None),
        ("cliente", "VARCHAR", 20),
    ]
    assert [(f.fragment_id, f.node_id, f.lower, f.upper) for f in ventas.fragments] == [
        ("ventas_f0", "n1", None, 5000),
        ("ventas_f1", "n2", 5000, None),
    ]
    assert node_catalog.tables == (node_catalog.table("items"),)


def test_create_table_on_nodo_es_una_tabla_single(node_catalog):
    sql = "CREATE TABLE Notas (codigo INT PRIMARY KEY, texto VARCHAR(60)) USING SEQUENTIAL ON n2"
    notas = ddl(node_catalog, sql).table("Notas")

    assert (notas.mode, notas.storage) == ("single", "sequential")
    assert [(f.fragment_id, f.node_id, f.lower, f.upper) for f in notas.fragments] == [
        ("notas", "n2", None, None)
    ]


def test_create_index_y_drop_table(node_catalog):
    catalog = ddl(node_catalog, VENTAS)
    catalog = ddl(catalog, "CREATE INDEX por_monto ON ventas (monto) USING BPLUS")
    (index,) = catalog.table("ventas").indexes
    assert (index.name, index.column, index.structure, index.supports_range) == (
        "por_monto",
        "monto",
        "bplus_unclustered",
        True,
    )
    assert catalog.catalog_version == 3

    catalog = ddl(catalog, "DROP TABLE ventas")
    assert [t.name for t in catalog.tables] == ["items"]
    assert catalog.catalog_version == 4


@pytest.mark.parametrize(
    ("sql", "message", "where"),
    [
        (VENTAS.replace("ventas (", "items ("), "ya existe", "items"),
        (VENTAS.replace("ON n2", "ON n9"), "el nodo 'n9' no existe; los nodos son n1, n2", "n9"),
        (VENTAS.replace("ON n2", "ON n1"), "n1 ya tiene ventas_f0", "n1"),
        (VENTAS.replace("ventas_f1", "items_f0"), "ya existe un fragmento", "items_f0"),
        (VENTAS.replace("ventas_f1", "Ventas_F1"), "minúsculas", "Ventas_F1"),
        (VENTAS.replace("ventas_f1", "ventas_f0"), "ya existe un fragmento", "ventas_f0"),
        (VENTAS.replace("(MAXVALUE)", "(9000)"), "MAXVALUE", "9000"),
        (VENTAS.replace("(5000)", "(MAXVALUE)"), "solo la última", None),
        (VENTAS.replace("(5000)", "(-2147483648)"), "el límite debe estar entre", "-2147483648"),
        (VENTAS.replace("(5000)", "(2147483648)"), "el límite debe estar entre", "2147483648"),
        (
            (
                "CREATE TABLE t (id INT PRIMARY KEY) PARTITION BY RANGE (id) "
                "(PARTITION t_f0 VALUES LESS THAN (MAXVALUE) ON n1)"
            ),
            "al menos dos particiones",
            None,
        ),
        ("DROP TABLE nada", "la tabla 'nada' no existe", "nada"),
        ("CREATE INDEX i ON nada (x) USING HASH", "la tabla 'nada' no existe", "nada"),
        ("CREATE INDEX por_id ON items (nombre) USING HASH", "ya existe en items", "por_id"),
        ("CREATE INDEX i ON items (falta) USING HASH", "no existe", "falta"),
        ("CREATE INDEX i ON items (nombre) USING RTREE", "RTREE requiere", "nombre"),
        ("CREATE TABLE items2 (id INT, x INT) ON n1", "exactamente una PRIMARY KEY", None),
    ],
)
def test_errores_semanticos_senalan_la_parte_culpable(node_catalog, sql, message, where):
    text, span_text = failure(node_catalog, sql)
    assert message in text
    if where is not None:
        assert span_text == where


def test_limites_que_no_crecen_con_tres_nodos(node_catalog):
    data = node_catalog.model_dump(mode="json")
    data["nodes"].append({"node_id": "n3", "base_url": "http://n3:8000"})
    catalog = type(node_catalog).model_validate(data)
    sql = VENTAS.replace(
        "(5000) ON n1,", "(5000) ON n1,\n  PARTITION ventas_fx VALUES LESS THAN (10) ON n3,"
    )
    text, span_text = failure(catalog, sql)
    assert (text, span_text) == ("los límites deben crecer: 10 no es mayor que 5000", "10")


@pytest.mark.parametrize(
    ("sql", "message", "where"),
    [
        ("CREATE TABLE t (id INT PRIMARY KEY)", "ON nodo o PARTITION BY RANGE", None),
        (VENTAS.replace("RANGE (id)", "RANGE (monto)"), "clave primaria (id)", "monto"),
        (
            VENTAS.replace(
                "id INT PRIMARY KEY, monto DOUBLE", "id INT, monto DOUBLE PRIMARY KEY"
            ).replace("RANGE (id)", "RANGE (monto)"),
            "requiere una clave primaria INT",
            "monto",
        ),
        (VENTAS.replace(")\nPARTITION", ") USING SEQUENTIAL\nPARTITION"), "HEAP", None),
        (
            "CREATE INDEX i ON items (nombre) USING FULLTEXT",
            "FULLTEXT no se admite en una tabla particionada",
            None,
        ),
    ],
)
def test_lo_que_v1_no_admite_sale_como_unsupported(node_catalog, sql, message, where):
    text, span_text = failure(node_catalog, sql, SQLUnsupportedError)
    assert text.startswith("[UNSUPPORTED_DISTRIBUTED]")
    assert message in text
    if where is not None:
        assert span_text == where


def test_hnsw_con_otra_metrica_y_fulltext_en_ingles_no_se_admiten(node_catalog):
    catalog = ddl(
        node_catalog,
        "CREATE TABLE fotos (id INT PRIMARY KEY, v VECTOR(3), t VARCHAR(20)) ON n1",
    )
    for sql, message in [
        ("CREATE INDEX h ON fotos (v) USING HNSW WITH METRIC=euclidean", "coseno"),
        ("CREATE INDEX f ON fotos (t) USING FULLTEXT WITH LANGUAGE=english", "idioma"),
    ]:
        text, _ = failure(catalog, sql, SQLUnsupportedError)
        assert message in text
    catalog = ddl(catalog, "CREATE INDEX f ON fotos (t) USING FULLTEXT")
    assert catalog.table("fotos").indexes[0].structure == "inverted_index"


def test_cambios_por_nodo(node_catalog):
    with_sales = ddl(node_catalog, VENTAS)
    notes = ddl(with_sales, "CREATE TABLE notas (id INT PRIMARY KEY) ON n2")

    n1 = changes_for_node(with_sales, notes, "n1")
    assert n1.empty
    n2 = changes_for_node(with_sales, notes, "n2")
    assert [t.name for t in n2.create_tables] == ["notas"]

    indexed = ddl(notes, "CREATE INDEX por_cliente ON ventas (cliente) USING HASH")
    for node in ("n1", "n2"):
        assert changes_for_node(notes, indexed, node).create_indexes == (("ventas", "por_cliente"),)

    # Deshacer el índice: el catálogo viejo con una versión mayor lo quita.
    undo = notes.model_copy(update={"catalog_version": indexed.catalog_version + 1})
    assert changes_for_node(indexed, undo, "n1").drop_indexes == (("ventas", "por_cliente"),)

    dropped = ddl(indexed, "DROP TABLE ventas")
    assert changes_for_node(indexed, dropped, "n1").drop_tables == ("ventas",)
    assert changes_for_node(indexed, dropped, "n2").drop_tables == ("ventas",)


def test_una_tabla_nueva_llega_con_sus_indices(node_catalog):
    empty = node_catalog.model_copy(update={"tables": ()})
    changes = changes_for_node(empty, node_catalog.model_copy(update={"catalog_version": 2}), "n1")
    assert [t.name for t in changes.create_tables] == ["items"]
    assert changes.create_indexes == (("items", "por_id"), ("items", "por_nombre"))


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda c: c.model_copy(update={"catalog_version": 1}), "no es mayor"),
        (lambda c: c.model_copy(update={"nodes": c.nodes[::-1]}), "nodos"),
        (
            lambda c: c.model_copy(
                update={
                    "tables": (
                        c.table("items").model_copy(
                            update={"fragments": c.table("items").fragments[::-1]}
                        ),
                    )
                }
            ),
            "cambió más que sus índices",
        ),
    ],
)
def test_cambios_que_v1_no_aplica(node_catalog, change, message):
    new = change(node_catalog.model_copy(update={"catalog_version": 2}))
    with pytest.raises(CatalogChangeError, match=message):
        changes_for_node(node_catalog, new, "n1")


def test_sql_generado_reparsea_igual(node_catalog):
    catalog = ddl(node_catalog, VENTAS)
    ventas = catalog.table("ventas")
    sql = create_table_sql(ventas)
    assert sql == (
        "CREATE TABLE ventas (id INT PRIMARY KEY, monto DOUBLE, cliente VARCHAR(20)) USING HEAP"
    )
    assert parse_sql(sql).distribution is None
    items = node_catalog.table("items")
    assert create_index_sql(items, "por_nombre") == (
        "CREATE INDEX por_nombre ON items (nombre) USING HASH"
    )
