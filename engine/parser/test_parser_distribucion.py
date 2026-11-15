"""Gramatica de CREATE TABLE distribuido (#296): PARTITION BY RANGE y ON nodo."""

import pytest

from engine.parser import parse_sql
from engine.parser.ast import CreateTableStatement, NodePlacement, RangeDistribution
from engine.parser.errors import SQLParseError

RANGO = """CREATE TABLE ventas (id INT PRIMARY KEY, monto DOUBLE) USING HEAP
PARTITION BY RANGE (id) (
  PARTITION ventas_f0 VALUES LESS THAN (-5) ON n1,
  PARTITION ventas_f1 VALUES LESS THAN (5000) ON n2,
  PARTITION ventas_f2 VALUES LESS THAN (MAXVALUE) ON n3
);"""


def test_partition_by_range_guarda_columna_limites_y_nodos() -> None:
    statement = parse_sql(RANGO)

    assert isinstance(statement, CreateTableStatement)
    distribution = statement.distribution
    assert isinstance(distribution, RangeDistribution)
    assert distribution.column.name == "id"
    assert [p.name.name for p in distribution.partitions] == [
        "ventas_f0",
        "ventas_f1",
        "ventas_f2",
    ]
    assert [p.upper and p.upper.value for p in distribution.partitions] == [-5, 5000, None]
    assert [p.node.name for p in distribution.partitions] == ["n1", "n2", "n3"]
    assert statement.span.end == RANGO.index(";")
    assert RANGO[distribution.span.start : distribution.span.end].startswith("PARTITION BY")


def test_on_nodo_ubica_la_tabla_entera() -> None:
    sql = "create table notas (codigo int primary key) on n2"
    statement = parse_sql(sql)

    assert isinstance(statement.distribution, NodePlacement)
    assert statement.distribution.node.name == "n2"
    assert statement.span.end == len(sql)


def test_sin_distribucion_es_la_tabla_local_de_siempre() -> None:
    assert parse_sql("CREATE TABLE t (id INT PRIMARY KEY)").distribution is None


def test_las_palabras_de_particion_no_son_reservadas() -> None:
    statement = parse_sql(
        "CREATE TABLE partition (range INT PRIMARY KEY, maxvalue INT, less INT, than INT)"
    )
    assert [c.name.name for c in statement.columns] == ["range", "maxvalue", "less", "than"]


PREFIJO = "CREATE TABLE t (id INT) PARTITION BY RANGE (id) "


@pytest.mark.parametrize(
    ("sql", "message", "lexeme"),
    [
        ("CREATE TABLE t (id INT) PARTITION RANGE (id) ()", "BY", "RANGE"),
        ("CREATE TABLE t (id INT) PARTITION BY HASH (id) ()", "RANGE", "HASH"),
        (f"{PREFIJO}(PARTITION a VALUES LESS THAN ('x') ON n1)", "entero o MAXVALUE", "'x'"),
        (f"{PREFIJO}(PARTITION a VALUES LESS THAN (5) n1)", "ON nodo", "n1"),
        (f"{PREFIJO}(PARTITION a VALUES THAN (5) ON n1)", "LESS THAN", "THAN"),
        ("CREATE TABLE t (id INT) ON", "nodo", ""),
    ],
)
def test_errores_de_sintaxis_senalan_el_token(sql: str, message: str, lexeme: str) -> None:
    with pytest.raises(SQLParseError) as caught:
        parse_sql(sql)
    assert message in caught.value.message
    assert sql[caught.value.span.start : caught.value.span.end] == lexeme
