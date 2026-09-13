"""Pruebas E2E de CREATE TABLE e INSERT INTO contra el core compilado."""

from datetime import date

import pytest

from engine.executor import QueryProcessor

quipudb = pytest.importorskip(
    "quipudb_native",
    reason="los bindings no estan compilados: cmake -DQUIPUDB_BUILD_PYTHON=ON",
)


@pytest.fixture()
def db(tmp_path):
    return quipudb.Database(tmp_path / "catalogo.txt")


@pytest.mark.parametrize(
    ("sql_storage", "native_storage"),
    [
        ("HEAP", quipudb.kind.HEAP),
        ("SEQUENTIAL", quipudb.kind.SEQUENTIAL),
    ],
)
def test_create_table_respeta_la_organizacion_fisica(db, sql_storage, native_storage):
    result = QueryProcessor(db).execute(
        f"CREATE TABLE datos (id INT PRIMARY KEY, nombre VARCHAR(20)) USING {sql_storage}"
    )

    assert result.columns == ()
    assert result.rows == ()
    assert result.affected_rows == 0
    assert db.table_info("datos").storage == native_storage


def test_create_fallido_no_deja_tabla_fantasma_ni_borra_ruta_ajena(db, tmp_path):
    occupied = tmp_path / "datos.heap"
    occupied.mkdir()
    processor = QueryProcessor(db)

    with pytest.raises(quipudb.IoError):
        processor.execute("CREATE TABLE datos (id INT PRIMARY KEY) USING HEAP")

    assert not db.has_table("datos")
    assert occupied.is_dir()
    occupied.rmdir()
    processor.execute("CREATE TABLE datos (id INT PRIMARY KEY) USING HEAP")
    assert db.has_table("datos")


def test_insert_escribe_y_permite_leer_los_cinco_tipos(db):
    processor = QueryProcessor(db)
    processor.execute(
        """CREATE TABLE alumnos (
            id INT PRIMARY KEY,
            promedio DOUBLE,
            nombre VARCHAR(32),
            activo BOOL,
            ingreso DATE
        ) USING HEAP"""
    )

    result = processor.execute(
        "INSERT INTO alumnos VALUES (7, 18.5, 'O''Brien', TRUE, DATE '2026-09-13')"
    )

    assert result.affected_rows == 1
    (row,) = db.table("alumnos").search(7)
    assert row[:4] == [7, 18.5, "O'Brien", True]
    assert row[4] == quipudb.Date((date(2026, 9, 13) - date(1970, 1, 1)).days)


def test_datos_sql_sobreviven_flush_cierre_y_reapertura(tmp_path):
    catalog = tmp_path / "catalogo.txt"
    db = quipudb.Database(catalog)
    processor = QueryProcessor(db)
    processor.execute(
        "CREATE TABLE eventos (id INT PRIMARY KEY, puntaje DOUBLE, nombre VARCHAR(20), "
        "activo BOOL, fecha DATE)"
    )
    db.create_index(
        "eventos",
        "por_nombre",
        "nombre",
        quipudb.kind.EXTENDIBLE_HASH,
    )
    processor.execute("INSERT INTO eventos VALUES (1, 9.5, 'inicio', TRUE, DATE '2026-09-13')")
    db.flush()
    db.close("eventos")
    del processor, db

    reopened = quipudb.Database(catalog)
    expected_date = quipudb.Date((date(2026, 9, 13) - date(1970, 1, 1)).days)
    expected_row = [1, 9.5, "inicio", True, expected_date]
    assert reopened.table("eventos").search(1) == [expected_row]

    (rid,) = reopened.index("eventos", "por_nombre").search("inicio")
    assert reopened.table("eventos").read(rid) == expected_row


def test_insert_actualiza_todos_los_indices_secundarios_existentes(db):
    processor = QueryProcessor(db)
    processor.execute("CREATE TABLE personas (id INT PRIMARY KEY, nombre VARCHAR(20)) USING HEAP")
    by_name = db.create_index(
        "personas",
        "por_nombre",
        "nombre",
        quipudb.kind.EXTENDIBLE_HASH,
    )
    by_id = db.create_index(
        "personas",
        "por_id",
        "id",
        quipudb.kind.BPLUS_UNCLUSTERED,
    )

    result = processor.execute("INSERT INTO personas VALUES (1, 'Ada')")

    assert result.affected_rows == 1
    (name_rid,) = by_name.search("Ada")
    (id_rid,) = by_id.search(1)
    assert name_rid == id_rid
    assert db.table("personas").read(name_rid) == [1, "Ada"]
