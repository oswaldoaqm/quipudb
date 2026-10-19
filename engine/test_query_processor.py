"""Pruebas E2E del Query Processor contra el core compilado."""

from datetime import date

import pytest

from engine.executor import QueryProcessor, QueryResult
from engine.parser import SQLParseError, SQLSemanticError, SqlTypeName
from engine.transactions import TransactionError

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


def test_point_se_crea_inserta_filtra_y_recupera_por_sql(db):
    processor = QueryProcessor(db)
    processor.execute(
        "CREATE TABLE lugares (id INT PRIMARY KEY, nombre VARCHAR(20), ubicacion POINT)"
    )
    processor.execute("INSERT INTO lugares VALUES (1, 'UTEC', POINT(-12.135, -77.022))")
    processor.execute("INSERT INTO lugares VALUES (2, 'Centro', POINT(-12.0464, -77.0428))")

    result = processor.execute(
        "SELECT nombre, ubicacion FROM lugares "
        "WHERE ubicacion = POINT(-12.0464, -77.0428)"
    )

    assert result.columns == ("nombre", "ubicacion")
    assert result.column_types == (SqlTypeName.VARCHAR, SqlTypeName.POINT)
    assert result.rows[0][0] == "Centro"
    assert result.rows[0][1].latitude == pytest.approx(-12.0464)
    assert result.rows[0][1].longitude == pytest.approx(-77.0428)
    assert db.table_info("lugares").schema.columns[2].type == quipudb.DataType.POINT


def test_lote_inserta_varias_filas_con_una_sola_llamada(db):
    processor = QueryProcessor(db)
    processor.execute("CREATE TABLE alumnos (id INT PRIMARY KEY, nombre VARCHAR(20))")

    result = processor.execute(
        "-- carga por lote\n"
        "INSERT INTO alumnos VALUES (1, 'Ada');\n"
        "INSERT INTO alumnos VALUES (2, 'Grace');\n"
        "INSERT INTO alumnos VALUES (3, 'Edsger');"
    )

    assert result.affected_rows == 3
    assert db.table("alumnos").scan() == [
        [1, "Ada"],
        [2, "Grace"],
        [3, "Edsger"],
    ]


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


@pytest.mark.parametrize(
    ("sql_kind", "native_kind"),
    [
        ("BPLUS", quipudb.kind.BPLUS_UNCLUSTERED),
        ("HASH", quipudb.kind.EXTENDIBLE_HASH),
    ],
)
def test_create_index_sql_construye_sobre_datos_existentes(
    db,
    sql_kind,
    native_kind,
):
    processor = QueryProcessor(db)
    processor.execute("CREATE TABLE personas (id INT PRIMARY KEY, nombre VARCHAR(20))")
    processor.execute("INSERT INTO personas VALUES (1, 'Ada')")
    processor.execute("INSERT INTO personas VALUES (2, 'Grace')")

    result = processor.execute(
        f"CREATE INDEX por_nombre ON personas (nombre) USING {sql_kind}"
    )

    assert result == QueryResult()
    info = db.table_info("personas")
    assert [(index.name, index.column, index.kind) for index in info.indexes] == [
        ("por_nombre", 1, native_kind)
    ]
    (rid,) = db.index("personas", "por_nombre").search("Ada")
    assert db.table("personas").read(rid) == [1, "Ada"]


def test_create_index_sql_es_usado_por_select_y_explain(db):
    processor = QueryProcessor(db)
    processor.execute("CREATE TABLE personas (id INT PRIMARY KEY, nombre VARCHAR(20))")
    processor.execute("INSERT INTO personas VALUES (1, 'Ada')")
    processor.execute("CREATE INDEX por_nombre ON personas (nombre) USING HASH")

    explained = processor.execute(
        "EXPLAIN SELECT id FROM personas WHERE nombre = 'Ada'"
    )
    selected = processor.execute("SELECT id FROM personas WHERE nombre = 'Ada'")

    assert explained.plan is not None
    assert [step.op.value for step in explained.plan.root.walk()] == [
        "index_search",
        "fetch",
        "project",
    ]
    assert explained.plan.root.subtree_stats().records_examined == 0
    assert selected.rows == ((1,),)
    assert selected.plan is not None
    assert selected.plan.root.walk()[0].structure.value == quipudb.kind.EXTENDIBLE_HASH


def test_explain_analyze_ejecuta_select_y_mide_plan_real(db):
    processor = QueryProcessor(db)
    processor.execute("CREATE TABLE personas (id INT PRIMARY KEY, nombre VARCHAR(20))")
    processor.execute("INSERT INTO personas VALUES (1, 'Ada')")
    processor.execute("INSERT INTO personas VALUES (2, 'Grace')")

    result = processor.execute("EXPLAIN ANALYZE SELECT nombre FROM personas")

    assert result.rows == ()
    assert result.plan is not None
    assert result.plan.query == "SELECT nombre FROM personas"
    assert result.plan.root.subtree_stats().records_examined >= 2


def test_create_index_sql_traduce_restriccion_de_tabla_heap(db):
    processor = QueryProcessor(db)
    processor.execute(
        "CREATE TABLE personas (id INT PRIMARY KEY, nombre VARCHAR(20)) USING SEQUENTIAL"
    )

    with pytest.raises(SQLSemanticError, match="no se pudo crear el indice"):
        processor.execute("CREATE INDEX por_nombre ON personas (nombre) USING HASH")


def test_drop_table_sql_borra_tabla_y_archivos_de_indices(tmp_path):
    db = quipudb.Database(tmp_path / "catalogo.txt")
    processor = QueryProcessor(db)
    processor.execute("CREATE TABLE datos (id INT PRIMARY KEY, nombre VARCHAR(20)) USING HEAP")
    db.create_index("datos", "por_nombre", "nombre", quipudb.kind.EXTENDIBLE_HASH)
    info = db.table_info("datos")
    paths = [tmp_path / info.file, *(tmp_path / index.file for index in info.indexes)]
    assert all(path.exists() for path in paths)

    result = processor.execute("DROP TABLE datos")

    assert result.affected_rows == 0
    assert not db.has_table("datos")
    assert all(not path.exists() for path in paths)


def test_delete_multilinea_con_comentarios_actualiza_indice(db):
    processor = QueryProcessor(db)
    processor.execute("CREATE TABLE datos (id INT PRIMARY KEY, nombre VARCHAR(20)) USING HEAP")
    index = db.create_index(
        "datos", "por_nombre", "nombre", quipudb.kind.EXTENDIBLE_HASH
    )
    processor.execute("INSERT INTO datos VALUES (1, 'Ada')")
    processor.execute("INSERT INTO datos VALUES (2, 'Grace')")

    result = processor.execute(
        "-- comentario inicial\n"
        "DELETE\n"
        "FROM datos /* comentario de bloque */\n"
        "WHERE nombre = 'Ada'"
    )

    assert result.affected_rows == 1
    assert db.table("datos").search(1) == []
    assert index.search("Ada") == []


def _create_select_table(db, storage):
    processor = QueryProcessor(db)
    if storage == quipudb.kind.BPLUS_CLUSTERED:
        schema = quipudb.Schema(
            "datos",
            [
                quipudb.Column("id", quipudb.DataType.INT),
                quipudb.Column("promedio", quipudb.DataType.DOUBLE),
                quipudb.Column("nombre", quipudb.DataType.VARCHAR, 20),
                quipudb.Column("activo", quipudb.DataType.BOOL),
                quipudb.Column("ingreso", quipudb.DataType.DATE),
            ],
            0,
        )
        db.create_table(schema, storage)
    else:
        sql_storage = "HEAP" if storage == quipudb.kind.HEAP else "SEQUENTIAL"
        processor.execute(
            "CREATE TABLE datos (id INT PRIMARY KEY, promedio DOUBLE, "
            "nombre VARCHAR(20), activo BOOL, ingreso DATE) "
            f"USING {sql_storage}"
        )

    rows = [
        "(1, 10, 'Ada', FALSE, DATE '2024-01-01')",
        "(2, 15, 'Luis', TRUE, DATE '2024-02-01')",
        "(3, 15, 'Zoe', FALSE, DATE '2024-03-01')",
        "(4, 20, 'Nora', TRUE, DATE '2024-04-01')",
    ]
    for row in rows:
        processor.execute(f"INSERT INTO datos VALUES {row}")
    return processor


def _ids(result):
    return {row[0] for row in result.rows}


def test_select_scan_proyecta_y_convierte_los_cinco_tipos(db):
    processor = _create_select_table(db, quipudb.kind.HEAP)

    wildcard = processor.execute("SELECT * FROM datos WHERE id = 2")
    projected = processor.execute("SELECT nombre, id, nombre FROM datos")

    assert wildcard.columns == ("id", "promedio", "nombre", "activo", "ingreso")
    assert wildcard.rows == ((2, 15.0, "Luis", True, date(2024, 2, 1)),)
    assert wildcard.affected_rows == 0
    assert wildcard.plan is not None
    assert [step.op.value for step in wildcard.plan.root.walk()] == ["search"]

    assert projected.columns == ("nombre", "id", "nombre")
    assert set(projected.rows) == {
        ("Ada", 1, "Ada"),
        ("Luis", 2, "Luis"),
        ("Zoe", 3, "Zoe"),
        ("Nora", 4, "Nora"),
    }
    assert projected.plan is not None
    assert [step.op.value for step in projected.plan.root.walk()] == ["scan", "project"]


@pytest.mark.parametrize(
    "storage",
    [
        quipudb.kind.HEAP,
        quipudb.kind.SEQUENTIAL,
        quipudb.kind.BPLUS_CLUSTERED,
    ],
)
def test_select_pk_elige_search_y_range_en_cada_organizacion(db, storage):
    processor = _create_select_table(db, storage)

    equality = processor.execute("SELECT * FROM datos WHERE id = 3")
    between = processor.execute("SELECT * FROM datos WHERE id BETWEEN 2 AND 4")

    assert _ids(equality) == {3}
    assert _ids(between) == {2, 3, 4}
    assert equality.plan is not None
    assert between.plan is not None
    assert equality.plan.root.op.value == "search"
    assert equality.plan.root.structure.value == storage
    assert between.plan.root.op.value == "range_search"
    assert between.plan.root.structure.value == storage


@pytest.mark.parametrize(
    ("operator", "expected", "ops"),
    [
        ("<", {1, 2}, ["range_search", "filter"]),
        ("<=", {1, 2, 3}, ["range_search"]),
        (">", {4}, ["range_search", "filter"]),
        (">=", {3, 4}, ["range_search"]),
    ],
)
def test_select_pk_respeta_operadores_estrictos_e_inclusivos(db, operator, expected, ops):
    processor = _create_select_table(db, quipudb.kind.SEQUENTIAL)

    result = processor.execute(f"SELECT * FROM datos WHERE id {operator} 3")

    assert _ids(result) == expected
    assert result.plan is not None
    assert [step.op.value for step in result.plan.root.walk()] == ops


def test_select_secundario_prefiere_hash_para_igualdad_y_bplus_para_rango(db):
    processor = _create_select_table(db, quipudb.kind.HEAP)
    db.create_index("datos", "z_promedio_bplus", "promedio", quipudb.kind.BPLUS_UNCLUSTERED)
    db.create_index("datos", "a_promedio_hash", "promedio", quipudb.kind.EXTENDIBLE_HASH)

    equality = processor.execute("SELECT id FROM datos WHERE promedio = 15")
    between = processor.execute("SELECT id FROM datos WHERE promedio BETWEEN 15 AND 20")
    strict = processor.execute("SELECT id FROM datos WHERE promedio < 15")

    assert {row[0] for row in equality.rows} == {2, 3}
    assert {row[0] for row in between.rows} == {2, 3, 4}
    assert {row[0] for row in strict.rows} == {1}
    assert equality.plan is not None
    assert between.plan is not None
    assert strict.plan is not None
    assert [step.op.value for step in equality.plan.root.walk()] == [
        "index_search",
        "fetch",
        "project",
    ]
    assert equality.plan.root.walk()[0].structure.value == quipudb.kind.EXTENDIBLE_HASH
    assert [step.op.value for step in between.plan.root.walk()] == [
        "index_range",
        "fetch",
        "project",
    ]
    assert between.plan.root.walk()[0].structure.value == quipudb.kind.BPLUS_UNCLUSTERED
    assert [step.op.value for step in strict.plan.root.walk()] == [
        "index_range",
        "fetch",
        "filter",
        "project",
    ]


def test_select_rango_con_solo_hash_hace_scan_y_filter(db):
    processor = _create_select_table(db, quipudb.kind.HEAP)
    db.create_index("datos", "por_activo", "activo", quipudb.kind.EXTENDIBLE_HASH)

    result = processor.execute("SELECT id FROM datos WHERE activo > FALSE")

    assert {row[0] for row in result.rows} == {2, 4}
    assert result.plan is not None
    assert [step.op.value for step in result.plan.root.walk()] == [
        "scan",
        "filter",
        "project",
    ]
    assert quipudb.kind.EXTENDIBLE_HASH not in {
        structure.value for structure in result.plan.structures_used()
    }


def test_select_vacio_y_between_invertido_no_son_error(db):
    processor = _create_select_table(db, quipudb.kind.HEAP)
    db.create_index("datos", "por_nombre", "nombre", quipudb.kind.EXTENDIBLE_HASH)

    missing = processor.execute("SELECT * FROM datos WHERE nombre = 'nombre demasiado largo'")
    embedded_nul = processor.execute("SELECT * FROM datos WHERE nombre = 'A\0da'")
    inverted = processor.execute("SELECT * FROM datos WHERE id BETWEEN 4 AND 2")

    assert missing.rows == ()
    assert embedded_nul.rows == ()
    assert inverted.rows == ()
    assert missing.plan is not None
    assert embedded_nul.plan is not None
    assert inverted.plan is not None
    assert missing.plan.root.op.value == "filter"
    assert embedded_nul.plan.root.op.value == "filter"
    assert inverted.plan.root.op.value == "range_search"


def test_select_resetea_stats_y_suma_el_arbol_sin_contaminacion(db):
    processor = _create_select_table(db, quipudb.kind.HEAP)
    table = db.table("datos")
    table.scan()

    first = processor.execute("SELECT id FROM datos WHERE nombre < 'N'")
    table.scan()
    second = processor.execute("SELECT id FROM datos WHERE nombre < 'N'")

    assert first.plan is not None
    assert second.plan is not None
    assert first.plan.root.subtree_stats() == second.plan.root.subtree_stats()
    assert first.plan.to_dict()["totals"] == first.plan.root.subtree_stats().to_dict()
    assert first.plan.time_ms >= first.plan.root.subtree_time_ms()
    assert all(step.time_ms >= 0 for step in first.plan.root.walk())


def test_select_por_indice_funciona_despues_de_reabrir(tmp_path):
    catalog = tmp_path / "catalogo.txt"
    db = quipudb.Database(catalog)
    processor = _create_select_table(db, quipudb.kind.HEAP)
    db.create_index("datos", "por_promedio", "promedio", quipudb.kind.BPLUS_UNCLUSTERED)
    db.flush()
    db.close("datos")
    del processor, db

    reopened = quipudb.Database(catalog)
    result = QueryProcessor(reopened).execute(
        "SELECT id, ingreso FROM datos WHERE promedio BETWEEN 15 AND 20"
    )

    assert set(result.rows) == {
        (2, date(2024, 2, 1)),
        (3, date(2024, 3, 1)),
        (4, date(2024, 4, 1)),
    }
    assert result.plan is not None
    assert result.plan.root.walk()[0].op.value == "index_range"


@pytest.mark.parametrize(
    "storage",
    [
        quipudb.kind.HEAP,
        quipudb.kind.SEQUENTIAL,
        quipudb.kind.BPLUS_CLUSTERED,
    ],
)
def test_delete_pk_por_igualdad_y_rango_en_cada_organizacion(db, storage):
    processor = _create_select_table(db, storage)

    equality = processor.execute("DELETE FROM datos WHERE id = 1")
    between = processor.execute("DELETE FROM datos WHERE id BETWEEN 3 AND 4")

    assert equality.affected_rows == 1
    assert between.affected_rows == 2
    assert equality.plan is None
    assert between.plan is None
    assert db.table("datos").search(1) == []
    assert db.table("datos").range_search(3, 4) == []
    assert [row[0] for row in db.table("datos").scan()] == [2]


@pytest.mark.parametrize(
    "storage",
    [quipudb.kind.SEQUENTIAL, quipudb.kind.BPLUS_CLUSTERED],
)
def test_delete_materializa_el_rango_antes_de_reorganizar_la_tabla(db, storage):
    schema = quipudb.Schema(
        "grande",
        [
            quipudb.Column("id", quipudb.DataType.INT),
            quipudb.Column("valor", quipudb.DataType.INT),
        ],
        0,
    )
    db.create_table(schema, storage)
    processor = QueryProcessor(db)
    for key in range(1, 121):
        processor.execute(f"INSERT INTO grande VALUES ({key}, {key % 5})")

    result = processor.execute("DELETE FROM grande WHERE id BETWEEN 21 AND 90")

    assert result.affected_rows == 70
    assert [row[0] for row in db.table("grande").scan()] == [
        *range(1, 21),
        *range(91, 121),
    ]


def test_delete_pk_actualiza_dos_indices_y_preserva_claves_repetidas(db):
    processor = _create_select_table(db, quipudb.kind.HEAP)
    by_name = db.create_index(
        "datos",
        "por_nombre",
        "nombre",
        quipudb.kind.EXTENDIBLE_HASH,
    )
    by_average = db.create_index(
        "datos",
        "por_promedio",
        "promedio",
        quipudb.kind.BPLUS_UNCLUSTERED,
    )

    result = processor.execute("DELETE FROM datos WHERE id = 2")

    assert result.affected_rows == 1
    assert db.table("datos").search(2) == []
    assert by_name.search("Luis") == []
    remaining_average = by_average.search(15.0)
    assert len(remaining_average) == 1
    assert db.table("datos").read(remaining_average[0])[0] == 3
    assert all(
        db.table("datos").read(rid) is not None
        for index in (by_name, by_average)
        for _key, rid in index.scan()
    )


def test_delete_secundario_usa_hash_para_igualdad_y_bplus_para_rango(db):
    processor = _create_select_table(db, quipudb.kind.HEAP)
    db.create_index(
        "datos",
        "por_activo",
        "activo",
        quipudb.kind.EXTENDIBLE_HASH,
    )
    db.create_index(
        "datos",
        "por_promedio",
        "promedio",
        quipudb.kind.BPLUS_UNCLUSTERED,
    )

    equality = processor.execute("DELETE FROM datos WHERE activo = FALSE")
    range_result = processor.execute(
        "DELETE FROM datos WHERE promedio BETWEEN 15 AND 20"
    )

    assert equality.affected_rows == 2
    assert range_result.affected_rows == 2
    assert db.table("datos").scan() == []
    for info in db.table_info("datos").indexes:
        assert db.index("datos", info.name).scan() == []


def test_delete_rango_con_solo_hash_hace_fallback_sin_dejar_rids(db):
    processor = _create_select_table(db, quipudb.kind.HEAP)
    by_active = db.create_index(
        "datos",
        "por_activo",
        "activo",
        quipudb.kind.EXTENDIBLE_HASH,
    )

    result = processor.execute("DELETE FROM datos WHERE activo > FALSE")

    assert result.affected_rows == 2
    assert {row[0] for row in db.table("datos").scan()} == {1, 3}
    assert by_active.search(True) == []
    false_rids = by_active.search(False)
    assert len(false_rids) == 2
    assert {db.table("datos").read(rid)[0] for rid in false_rids} == {1, 3}


def test_delete_cero_coincidencias_y_between_invertido_no_modifican(db):
    processor = _create_select_table(db, quipudb.kind.HEAP)
    index = db.create_index(
        "datos",
        "por_nombre",
        "nombre",
        quipudb.kind.EXTENDIBLE_HASH,
    )
    before_rows = db.table("datos").scan()
    before_entries = index.scan()

    missing = processor.execute("DELETE FROM datos WHERE nombre = 'ausente'")
    inverted = processor.execute("DELETE FROM datos WHERE id BETWEEN 4 AND 2")

    assert missing.affected_rows == 0
    assert inverted.affected_rows == 0
    assert db.table("datos").scan() == before_rows
    assert index.scan() == before_entries


def test_delete_e_indices_permanecen_consistentes_despues_de_reabrir(tmp_path):
    catalog = tmp_path / "catalogo.txt"
    db = quipudb.Database(catalog)
    processor = _create_select_table(db, quipudb.kind.HEAP)
    db.create_index(
        "datos",
        "por_promedio",
        "promedio",
        quipudb.kind.BPLUS_UNCLUSTERED,
    )
    db.create_index(
        "datos",
        "por_nombre",
        "nombre",
        quipudb.kind.EXTENDIBLE_HASH,
    )
    assert processor.execute("DELETE FROM datos WHERE promedio = 15").affected_rows == 2
    db.flush()
    db.close("datos")
    del processor, db

    reopened = quipudb.Database(catalog)
    table = reopened.table("datos")

    assert {row[0] for row in table.scan()} == {1, 4}
    assert reopened.index("datos", "por_promedio").search(15.0) == []
    assert reopened.index("datos", "por_nombre").search("Luis") == []
    assert reopened.index("datos", "por_nombre").search("Zoe") == []
    assert all(
        table.read(rid) is not None
        for info in reopened.table_info("datos").indexes
        for _key, rid in reopened.index("datos", info.name).scan()
    )


def test_begin_end_transaction_agrupa_inserts_y_los_confirma(db):
    processor = QueryProcessor(db)
    processor.execute("CREATE TABLE alumnos (id INT PRIMARY KEY, nombre VARCHAR(20)) USING HEAP")

    begin = processor.execute("BEGIN TRANSACTION")
    processor.execute("INSERT INTO alumnos VALUES (1, 'Ada')")
    processor.execute("INSERT INTO alumnos VALUES (2, 'Bob')")
    end = processor.execute("END TRANSACTION")

    assert begin == QueryResult()
    assert end == QueryResult()
    assert {row[0] for row in db.table("alumnos").scan()} == {1, 2}


def test_rollback_deshace_inserts_de_una_transaccion_sin_cerrar(db):
    processor = QueryProcessor(db)
    processor.execute("CREATE TABLE alumnos (id INT PRIMARY KEY, nombre VARCHAR(20)) USING HEAP")
    processor.execute("BEGIN TRANSACTION")
    processor.execute("INSERT INTO alumnos VALUES (1, 'Ada')")
    processor.execute("INSERT INTO alumnos VALUES (2, 'Bob')")

    processor.rollback()

    assert db.table("alumnos").scan() == []
    # el rollback dejo la transaccion cerrada: se puede abrir una nueva.
    processor.execute("BEGIN TRANSACTION")
    processor.execute("END TRANSACTION")


def test_error_de_dominio_dentro_de_transaccion_hace_rollback_automatico(db):
    processor = QueryProcessor(db)
    processor.execute("CREATE TABLE alumnos (id INT PRIMARY KEY, nombre VARCHAR(20)) USING HEAP")
    processor.execute("BEGIN TRANSACTION")
    processor.execute("INSERT INTO alumnos VALUES (1, 'Ada')")

    with pytest.raises(SQLSemanticError):
        processor.execute("INSERT INTO alumnos VALUES (1, 'Otra Ada')")

    assert db.table("alumnos").scan() == []
    # el error aborto la transaccion entera: no quedo una a medio cerrar.
    processor.execute("BEGIN TRANSACTION")
    processor.execute("END TRANSACTION")


def test_begin_transaction_anidada_es_error(db):
    processor = QueryProcessor(db)
    processor.execute("BEGIN TRANSACTION")

    with pytest.raises(TransactionError):
        processor.execute("BEGIN TRANSACTION")

    processor.rollback()


def test_end_transaction_sin_begin_es_error(db):
    processor = QueryProcessor(db)

    with pytest.raises(TransactionError):
        processor.execute("END TRANSACTION")


def test_rollback_sin_transaccion_activa_es_error(db):
    processor = QueryProcessor(db)

    with pytest.raises(TransactionError):
        processor.rollback()


def test_create_table_dentro_de_transaccion_es_error(db):
    processor = QueryProcessor(db)
    processor.execute("BEGIN TRANSACTION")

    with pytest.raises(TransactionError):
        processor.execute("CREATE TABLE alumnos (id INT PRIMARY KEY) USING HEAP")

    assert not db.has_table("alumnos")
    # el intento de CREATE TABLE no toco la transaccion activa.
    processor.execute("END TRANSACTION")


def test_delete_dentro_de_transaccion_se_revierte_con_rollback(db):
    processor = _create_select_table(db, quipudb.kind.HEAP)
    by_name = db.create_index("datos", "por_nombre", "nombre", quipudb.kind.EXTENDIBLE_HASH)

    processor.execute("BEGIN TRANSACTION")
    result = processor.execute("DELETE FROM datos WHERE id = 1")

    assert result.affected_rows == 1
    assert db.table("datos").search(1) == []
    assert by_name.search("Ada") == []

    processor.rollback()

    restored = db.table("datos").search(1)
    assert len(restored) == 1
    assert restored[0][0] == 1
    assert restored[0][2] == "Ada"
    (rid,) = by_name.search("Ada")
    assert db.table("datos").read(rid)[0] == 1


def test_column_types_viene_del_esquema_y_no_de_las_filas(db):
    """El Panel de Resultados necesita el tipo aunque no haya filas."""

    processor = QueryProcessor(db)
    processor.execute(
        "CREATE TABLE alumnos (codigo INT PRIMARY KEY, nombre VARCHAR(32), "
        "promedio DOUBLE, activo BOOL, ingreso DATE) USING HEAP"
    )
    processor.execute("INSERT INTO alumnos VALUES (1, 'ana', 15.6, TRUE, DATE '2026-01-05')")

    completo = processor.execute("SELECT * FROM alumnos")
    assert [tipo.value for tipo in completo.column_types] == [
        "INT",
        "VARCHAR",
        "DOUBLE",
        "BOOL",
        "DATE",
    ]

    vacio = processor.execute("SELECT nombre, promedio FROM alumnos WHERE codigo = 999")
    assert vacio.rows == ()
    assert [tipo.value for tipo in vacio.column_types] == ["VARCHAR", "DOUBLE"]


def test_column_types_de_un_group_by_tipa_cada_agregado(db):
    processor = QueryProcessor(db)
    processor.execute(
        "CREATE TABLE notas (id INT PRIMARY KEY, curso VARCHAR(16), nota DOUBLE) USING HEAP"
    )
    processor.execute("INSERT INTO notas VALUES (1, 'bd2', 18.0)")

    result = processor.execute(
        "SELECT curso, COUNT(*), AVG(nota) FROM notas GROUP BY curso"
    )

    assert result.columns == ("curso", "COUNT_all", "AVG_nota")
    assert [tipo.value for tipo in result.column_types] == ["VARCHAR", "INT", "DOUBLE"]


def test_una_sentencia_sin_filas_no_declara_tipos(db):
    processor = QueryProcessor(db)
    processor.execute("CREATE TABLE t (id INT PRIMARY KEY) USING HEAP")

    result = processor.execute("INSERT INTO t VALUES (1)")

    assert result.columns == ()
    assert result.column_types == ()
    assert result.affected_rows == 1


@pytest.mark.parametrize("estructura", [None, "HASH", "BPLUS"])
def test_cero_y_menos_cero_dan_lo_mismo_con_o_sin_indice(db, estructura) -> None:
    # 0.0 = -0.0 en SQL. El hash extensible los mandaba al mismo bucket pero
    # despues comparaba bytes, y con indice la consulta perdia una fila que
    # sin indice si devolvia.
    processor = QueryProcessor(db)
    processor.execute("CREATE TABLE precios (id INT PRIMARY KEY, precio DOUBLE) USING HEAP")
    if estructura is not None:
        processor.execute(f"CREATE INDEX por_precio ON precios (precio) USING {estructura}")
    processor.execute(
        "INSERT INTO precios VALUES (1, 0.0); INSERT INTO precios VALUES (2, -0.0);"
        "INSERT INTO precios VALUES (3, 1.5)"
    )

    for literal in ("0.0", "-0.0"):
        filas = processor.execute(f"SELECT id FROM precios WHERE precio = {literal}").rows
        assert sorted(filas) == [(1,), (2,)], (estructura, literal)

    assert processor.execute("DELETE FROM precios WHERE precio = 0").affected_rows == 2
    assert processor.execute("SELECT id FROM precios").rows == ((3,),)


# ---------------------------------------------------------------------------
# Indice de texto desde SQL (#167)
# ---------------------------------------------------------------------------

_DOCUMENTOS = (
    "INSERT INTO docs VALUES (1, 'Cargamento de oro dañado por el fuego');"
    "INSERT INTO docs VALUES (2, 'La entrega de la plata llegó en el camión color plata');"
    "INSERT INTO docs VALUES (3, 'El cargamento de oro llegó en un camión')"
)


def _docs(db) -> QueryProcessor:
    processor = QueryProcessor(db)
    processor.execute("CREATE TABLE docs (id INT PRIMARY KEY, texto VARCHAR(120)) USING HEAP")
    processor.execute(_DOCUMENTOS)
    return processor


def _ids_por_texto(db, consulta: str) -> list[int]:
    ix = db.index("docs", "por_texto")
    tabla = db.table("docs")
    return [tabla.read(rid)[0] for rid, _ in ix.search_text(consulta, quipudb.TextRanking.TF_IDF)]


@pytest.mark.parametrize("estructura", ["FULLTEXT", "INVERTED_INDEX"])
def test_create_index_fulltext_construye_y_queda_en_el_catalogo(db, estructura):
    processor = _docs(db)

    result = processor.execute(f"CREATE INDEX por_texto ON docs (texto) USING {estructura}")

    assert result == QueryResult()
    info = db.table_info("docs")
    assert [(index.name, index.column, index.kind) for index in info.indexes] == [
        ("por_texto", 1, quipudb.kind.INVERTED_INDEX)
    ]
    ix = db.index("docs", "por_texto")
    assert isinstance(ix, quipudb.TextIndex)
    assert ix.documents == 3
    assert _ids_por_texto(db, "oro plata camión") == [2, 3, 1]


def test_create_index_fulltext_sobre_columna_que_no_es_texto_es_error_ubicado(db):
    processor = _docs(db)
    sql = "CREATE INDEX por_id ON docs (id) USING FULLTEXT"

    with pytest.raises(SQLSemanticError, match="requiere una columna VARCHAR") as caught:
        processor.execute(sql)

    assert sql[caught.value.span.start : caught.value.span.end] == "id"
    assert (caught.value.span.line, caught.value.span.column) == (1, 30)
    assert db.table_info("docs").indexes == []


def test_un_segundo_indice_de_texto_en_la_columna_es_error_y_no_reconstruye(db):
    processor = _docs(db)
    processor.execute("CREATE INDEX por_texto ON docs (texto) USING FULLTEXT")
    processor.execute("INSERT INTO docs VALUES (4, 'mina de oro')")
    assert db.index("docs", "por_texto").stale
    sql = "CREATE INDEX otro ON docs (texto) USING FULLTEXT"

    with pytest.raises(SQLSemanticError, match="ya tiene el indice de texto 'por_texto'") as caught:
        processor.execute(sql)

    assert sql[caught.value.span.start : caught.value.span.end] == "texto"
    assert [index.name for index in db.table_info("docs").indexes] == ["por_texto"]
    # No se reconstruyo en silencio: sigue desactualizado hasta la proxima busqueda.
    assert db.index("docs", "por_texto").stale


def test_el_mismo_nombre_de_indice_tambien_es_error(db):
    processor = _docs(db)
    processor.execute("CREATE INDEX por_texto ON docs (texto) USING FULLTEXT")

    with pytest.raises(SQLSemanticError, match="por_texto"):
        processor.execute("CREATE INDEX por_texto ON docs (id) USING HASH")


def test_insert_y_delete_funcionan_con_indice_de_texto(db):
    # El DML le avisa de cada fila al indice de texto, que se marca
    # desactualizado; la busqueda lo reconstruye y ve los cambios.
    processor = _docs(db)
    processor.execute("CREATE INDEX por_texto ON docs (texto) USING FULLTEXT")

    assert processor.execute("INSERT INTO docs VALUES (4, 'mina de oro')").affected_rows == 1
    assert processor.execute("DELETE FROM docs WHERE id = 2").affected_rows == 1

    assert _ids_por_texto(db, "mina") == [4]
    assert _ids_por_texto(db, "plata") == []
    assert db.index("docs", "por_texto").documents == 3


def test_select_por_igualdad_sobre_la_columna_indexada_por_texto(db):
    # El indice de texto no responde igualdad: la consulta recorre y filtra.
    processor = _docs(db)
    processor.execute("CREATE INDEX por_texto ON docs (texto) USING FULLTEXT")

    result = processor.execute(
        "SELECT id FROM docs WHERE texto = 'El cargamento de oro llegó en un camión'"
    )

    assert result.rows == ((3,),)
    plan = processor.execute("EXPLAIN SELECT id FROM docs WHERE texto = 'x'").plan
    assert "inverted_index" not in str(plan)


def test_deshacer_una_transaccion_deja_el_indice_de_texto_coherente(db):
    processor = _docs(db)
    processor.execute("CREATE INDEX por_texto ON docs (texto) USING FULLTEXT")

    processor.execute("BEGIN TRANSACTION")
    processor.execute("INSERT INTO docs VALUES (4, 'mina de oro')")
    processor.rollback()

    assert _ids_por_texto(db, "mina") == []
    assert db.index("docs", "por_texto").documents == 3


def test_drop_table_borra_el_indice_de_texto_y_su_marca(db, tmp_path):
    processor = _docs(db)
    processor.execute("CREATE INDEX por_texto ON docs (texto) USING FULLTEXT")
    processor.execute("INSERT INTO docs VALUES (4, 'mina')")  # deja la marca

    processor.execute("DROP TABLE docs")

    assert sorted(p.name for p in tmp_path.iterdir()) == ["catalogo.txt"]


# ---------------------------------------------------------------------------
# MATCH ... USING TF_IDF | BM25 (#168)
# ---------------------------------------------------------------------------

_CORPUS = (
    "INSERT INTO arts VALUES (1, 'ana', 'Cargamento de oro dañado por el fuego');"
    "INSERT INTO arts VALUES (2, 'ana', 'La entrega de la plata llegó en el camión color plata');"
    "INSERT INTO arts VALUES (3, 'luis', 'El cargamento de oro llegó en un camión');"
    "INSERT INTO arts VALUES (4, 'luis', 'Bases de datos vectoriales y búsqueda de texto');"
    "INSERT INTO arts VALUES (5, 'eva', 'El índice invertido acelera la búsqueda en bases de datos')"
)


def _arts(db) -> QueryProcessor:
    processor = QueryProcessor(db)
    processor.execute(
        "CREATE TABLE arts (id INT PRIMARY KEY, autor VARCHAR(20), texto VARCHAR(160)) USING HEAP"
    )
    processor.execute(_CORPUS)
    processor.execute("CREATE INDEX por_texto ON arts (texto) USING FULLTEXT")
    return processor


def _ranking_nativo(db, consulta: str, ranking) -> list[int]:
    """El orden que da el indice directamente, sin pasar por SQL."""

    tabla = db.table("arts")
    resultado = db.index("arts", "por_texto").search_text(consulta, ranking)
    return [tabla.read(rid)[0] for rid, _ in resultado]


def test_match_la_consulta_del_enunciado(db):
    processor = _arts(db)

    result = processor.execute(
        "SELECT * FROM arts WHERE MATCH(texto, 'base de datos vectorial') USING TF_IDF LIMIT 10"
    )

    # Los dos documentos sobre bases de datos, el que ademas dice "vectoriales" primero.
    assert [row[0] for row in result.rows] == [4, 5]
    assert result.rows[0] == (4, "luis", "Bases de datos vectoriales y búsqueda de texto")
    assert [row[0] for row in result.rows] == _ranking_nativo(
        db, "base de datos vectorial", quipudb.TextRanking.TF_IDF
    )


@pytest.mark.parametrize("ranking", ["TF_IDF", "BM25"])
def test_match_sigue_el_orden_del_ranking_elegido(db, ranking):
    processor = _arts(db)

    result = processor.execute(f"SELECT id FROM arts WHERE MATCH(texto, 'oro camión') USING {ranking}")

    assert [row[0] for row in result.rows] == _ranking_nativo(
        db, "oro camión", getattr(quipudb.TextRanking, ranking)
    )
    assert sorted(row[0] for row in result.rows) == [1, 2, 3]


def test_sin_using_el_ranking_es_bm25(db):
    processor = _arts(db)

    sin_using = processor.execute("SELECT id FROM arts WHERE MATCH(texto, 'oro plata camión')")
    con_bm25 = processor.execute(
        "SELECT id FROM arts WHERE MATCH(texto, 'oro plata camión') USING BM25"
    )

    assert sin_using.rows == con_bm25.rows


def test_limit_corta_y_sin_limit_salen_todos(db):
    processor = _arts(db)

    todos = processor.execute("SELECT id FROM arts WHERE MATCH(texto, 'oro camión')").rows
    dos = processor.execute("SELECT id FROM arts WHERE MATCH(texto, 'oro camión') LIMIT 2").rows
    ninguno = processor.execute("SELECT id FROM arts WHERE MATCH(texto, 'helicóptero')").rows

    assert len(todos) == 3
    assert dos == todos[:2]
    assert ninguno == ()


def test_match_proyecta_columnas(db):
    processor = _arts(db)

    result = processor.execute(
        "SELECT autor FROM arts WHERE MATCH(texto, 'índice invertido') USING BM25 LIMIT 1"
    )

    assert result.columns == ("autor",)
    assert result.rows == (("eva",),)


def test_match_con_order_by_trae_todos_y_ordena(db):
    processor = _arts(db)

    result = processor.execute(
        "SELECT id FROM arts WHERE MATCH(texto, 'oro camión') ORDER BY id DESC LIMIT 2"
    )

    assert result.rows == ((3,), (2,))


def test_match_con_group_by(db):
    processor = _arts(db)

    result = processor.execute(
        "SELECT autor, COUNT(*) FROM arts WHERE MATCH(texto, 'oro camión') GROUP BY autor"
    )

    assert sorted(result.rows) == [("ana", 2), ("luis", 1)]


def test_match_queda_en_el_plan(db):
    processor = _arts(db)

    result = processor.execute("SELECT id FROM arts WHERE MATCH(texto, 'oro') USING TF_IDF")
    plan = result.plan.to_dict()
    explicado = processor.execute("EXPLAIN SELECT id FROM arts WHERE MATCH(texto, 'oro')")

    assert "text_search" in str(plan)
    assert "inverted_index" in str(plan)
    assert "text_search" in str(explicado.plan.to_dict())


def test_match_ve_las_filas_insertadas_despues_de_crear_el_indice(db):
    processor = _arts(db)
    processor.execute("INSERT INTO arts VALUES (6, 'eva', 'Una mina de oro en la sierra')")

    result = processor.execute("SELECT id FROM arts WHERE MATCH(texto, 'mina')")

    assert result.rows == ((6,),)


def test_match_sin_indice_de_texto_es_error_ubicado(db):
    processor = _arts(db)
    sql = "SELECT id FROM arts WHERE MATCH(autor, 'ana')"

    with pytest.raises(SQLSemanticError, match="no tiene un indice de texto") as caught:
        processor.execute(sql)

    assert sql[caught.value.span.start : caught.value.span.end] == "autor"
    assert (caught.value.span.line, caught.value.span.column) == (1, 33)


def test_match_sobre_columna_que_no_es_texto_es_error_ubicado(db):
    processor = _arts(db)
    sql = "SELECT id FROM arts WHERE MATCH(id, 'ana')"

    with pytest.raises(SQLSemanticError, match="VARCHAR") as caught:
        processor.execute(sql)

    assert sql[caught.value.span.start : caught.value.span.end] == "id"


def test_match_con_ranking_desconocido_es_error_de_sintaxis(db):
    processor = _arts(db)
    sql = "SELECT id FROM arts\nWHERE MATCH(texto, 'oro') USING COSENO"

    with pytest.raises(SQLParseError, match="TF_IDF o BM25") as caught:
        processor.execute(sql)

    assert (caught.value.span.line, caught.value.span.column) == (2, 33)
