"""Pruebas de los bindings del core (issue #23).

Estas pruebas son la razon por la que el CI compila los bindings. Sin ellas, el
job de Python queda verde mientras `bindings/module.cpp` no compila desde hace
semanas, que es justo el error que ya se cometio una vez con `ctest`.

Se saltan enteras si el modulo no esta compilado, para que quien trabaja solo en
`engine/` no necesite pybind11 -- pero el CI SI lo compila, asi que ahi no se
saltan nunca.
"""

from __future__ import annotations

import pytest

quipudb = pytest.importorskip(
    "quipudb_native",
    reason="los bindings no estan compilados: cmake -DQUIPUDB_BUILD_PYTHON=ON",
)


# ---------------------------------------------------------------------------
# Ayudas
# ---------------------------------------------------------------------------


def esquema_alumnos() -> quipudb.Schema:
    """Un esquema con los cinco tipos, para que nada quede sin ejercitar."""
    return quipudb.Schema(
        table_name="alumnos",
        columns=[
            quipudb.Column("codigo", quipudb.DataType.INT),
            quipudb.Column("nombre", quipudb.DataType.VARCHAR, 16),
            quipudb.Column("promedio", quipudb.DataType.DOUBLE),
            quipudb.Column("activo", quipudb.DataType.BOOL),
            quipudb.Column("ingreso", quipudb.DataType.DATE),
        ],
        key_column=0,
    )


def alumno(codigo: int) -> list:
    return [codigo, f"a{codigo}", 10.0 + codigo % 10, codigo % 2 == 0, quipudb.Date(20000 + codigo)]


@pytest.fixture()
def db(tmp_path):
    return quipudb.Database(tmp_path / "catalogo.txt")


# ---------------------------------------------------------------------------
# Conversion de tipos: los dos bugs que este issue arreglo
# ---------------------------------------------------------------------------


def test_un_bool_no_llega_como_entero(db):
    """En Python `bool` es subclase de `int`, asi que el caster automatico del
    variant aceptaba True como int32 y `INSERT ... VALUES (True)` moria con
    "se esperaba BOOL y llego INT"."""
    t = db.create_table(esquema_alumnos(), quipudb.kind.HEAP)
    t.insert([1, "ana", 15.5, True, quipudb.Date(20000)])

    (fila,) = t.search(1)
    assert fila[3] is True
    assert isinstance(fila[3], bool), "un BOOL volvio como entero"


def test_un_entero_en_una_columna_double_se_promueve(db):
    """`1` es un int legitimo, pero si la columna es DOUBLE lo que el usuario
    quiso decir es `1.0`. Se resuelve con el esquema al insertar, no
    reordenando el caster: reordenarlo romperia los INT de verdad."""
    t = db.create_table(esquema_alumnos(), quipudb.kind.HEAP)
    t.insert([1, "ana", 15, True, 20000])  # 15 y 20000 son int

    (fila,) = t.search(1)
    assert fila[2] == 15.0
    assert isinstance(fila[2], float), "el int no se promovio a DOUBLE"
    assert isinstance(fila[4], quipudb.Date), "el int no se promovio a DATE"


def test_los_cinco_tipos_vuelven_como_llegaron(db):
    t = db.create_table(esquema_alumnos(), quipudb.kind.HEAP)
    original = [7, "siete", 17.25, False, quipudb.Date(19000)]
    t.insert(original)

    (fila,) = t.search(7)
    assert fila[0] == 7
    assert fila[1] == "siete"
    assert fila[2] == pytest.approx(17.25)
    assert fila[3] is False
    assert fila[4].days == 19000


def test_un_entero_que_no_entra_en_int32_se_denuncia(db):
    """Truncar en silencio dejaria dos claves distintas colapsadas en una."""
    t = db.create_table(esquema_alumnos(), quipudb.kind.HEAP)
    with pytest.raises(ValueError):
        t.insert([2**40, "grande", 1.0, True, quipudb.Date(1)])


# ---------------------------------------------------------------------------
# Excepciones: una por una, no todas RuntimeError
# ---------------------------------------------------------------------------


def test_cada_error_del_core_tiene_su_excepcion(db):
    """Colapsarlas todas a RuntimeError obliga a leer el texto del mensaje para
    saber que paso, y eso convierte un mensaje en una API."""
    t = db.create_table(esquema_alumnos(), quipudb.kind.HEAP)
    t.insert(alumno(1))

    with pytest.raises(quipudb.DuplicateKey):
        t.insert(alumno(1))

    with pytest.raises(quipudb.InvalidRecord):
        t.insert([1, "solo dos"])

    with pytest.raises(quipudb.SchemaError):
        db.table("no_existe")


def test_todas_derivan_de_una_sola(db):
    """Quien no quiera distinguir captura QuipuDBError y ya."""
    with pytest.raises(quipudb.QuipuDBError):
        db.table("no_existe")


# ---------------------------------------------------------------------------
# Las tres organizaciones
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "organizacion",
    [quipudb.kind.HEAP, quipudb.kind.SEQUENTIAL, quipudb.kind.BPLUS_CLUSTERED],
)
def test_cada_organizacion_guarda_y_devuelve(db, organizacion):
    t = db.create_table(esquema_alumnos(), organizacion)
    assert t.kind == organizacion

    for c in range(1, 51):
        t.insert(alumno(c))
    assert t.size() == 50
    assert len(t) == 50

    (fila,) = t.search(25)
    assert fila[0] == 25
    assert len(t.scan()) == 50
    assert t.remove(25) == 1
    assert t.search(25) == []
    assert t.size() == 49


def test_el_rango_es_inclusivo_en_ambos_extremos(db):
    t = db.create_table(esquema_alumnos(), quipudb.kind.SEQUENTIAL)
    for c in range(1, 21):
        t.insert(alumno(c))
    codigos = sorted(f[0] for f in t.range_search(5, 10))
    assert codigos == [5, 6, 7, 8, 9, 10]


def test_no_encontrado_no_es_un_error(db):
    t = db.create_table(esquema_alumnos(), quipudb.kind.HEAP)
    t.insert(alumno(1))
    assert t.search(999) == []
    assert t.remove(999) == 0
    assert t.read(quipudb.RID(9999, 0)) is None


# ---------------------------------------------------------------------------
# Indices
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tipo", [quipudb.kind.BPLUS_UNCLUSTERED, quipudb.kind.EXTENDIBLE_HASH])
def test_cada_indice_se_abre_desde_el_catalogo(db, tipo):
    t = db.create_table(esquema_alumnos(), quipudb.kind.HEAP)
    for c in range(1, 101):
        t.insert(alumno(c))

    ix = db.create_index("alumnos", "por_promedio", "promedio", tipo)
    assert ix.kind == tipo
    assert ix.size() == 100, "el indice no se construyo sobre los datos existentes"
    assert db.index("alumnos", "por_promedio") is ix, "no devolvio el mismo objeto"

    rids = ix.search(15.0)
    assert rids, "no encontro las entradas de esa clave"
    for rid in rids:
        fila = t.read(rid)
        assert fila is not None, "el indice apunta a un registro que la tabla no ve"
        assert fila[2] == pytest.approx(15.0)


def test_el_hash_dice_que_no_soporta_rango(db):
    """No es una limitacion de la implementacion: el hash esparce a proposito,
    asi que claves contiguas caen en buckets sin relacion."""
    t = db.create_table(esquema_alumnos(), quipudb.kind.HEAP)
    for c in range(1, 21):
        t.insert(alumno(c))
    ix = db.create_index("alumnos", "ix", "promedio", quipudb.kind.EXTENDIBLE_HASH)

    assert ix.supports_range() is False
    with pytest.raises(quipudb.Unsupported):
        ix.range_search(10.0, 15.0)


def test_el_bplus_si_soporta_rango(db):
    t = db.create_table(esquema_alumnos(), quipudb.kind.HEAP)
    for c in range(1, 21):
        t.insert(alumno(c))
    ix = db.create_index("alumnos", "ix", "promedio", quipudb.kind.BPLUS_UNCLUSTERED)

    assert ix.supports_range() is True
    assert ix.range_search(10.0, 12.0), "el rango no devolvio nada"


def test_solo_se_puede_indexar_un_heap(db):
    """En el secuencial y en el B+ agrupado los registros se corren de sitio al
    insertar, asi que un RID guardado dejaria de valer."""
    db.create_table(esquema_alumnos(), quipudb.kind.SEQUENTIAL)
    with pytest.raises(quipudb.SchemaError):
        db.create_index("alumnos", "ix", "promedio", quipudb.kind.EXTENDIBLE_HASH)


def test_borrar_del_indice(db):
    t = db.create_table(esquema_alumnos(), quipudb.kind.HEAP)
    for c in range(1, 41):
        t.insert(alumno(c))
    ix = db.create_index("alumnos", "ix", "promedio", quipudb.kind.EXTENDIBLE_HASH)

    rids = ix.search(15.0)
    assert len(rids) >= 2
    assert ix.remove_one(15.0, rids[0]) is True
    assert len(ix.search(15.0)) == len(rids) - 1

    quitadas = ix.remove(15.0)
    assert quitadas == len(rids) - 1
    assert ix.search(15.0) == []


# ---------------------------------------------------------------------------
# Lo que el plan de ejecucion y los benchmarks consumen
# ---------------------------------------------------------------------------


def test_las_stats_alimentan_el_plan_de_ejecucion(db):
    """`as_dict()` devuelve exactamente los cuatro campos de `Stats` del
    ADR 0002, asi que el panel (#37) los consume sin traducir nada."""
    t = db.create_table(esquema_alumnos(), quipudb.kind.HEAP)
    for c in range(1, 101):
        t.insert(alumno(c))

    t.reset_stats()
    t.scan()
    stats = t.stats().as_dict()
    assert set(stats) == {
        "pages_read",
        "pages_written",
        "records_examined",
        "records_returned",
    }
    assert stats["pages_read"] > 0
    assert stats["records_returned"] == 100


def test_un_indice_lee_menos_paginas_que_un_scan(db):
    """Es la comparacion que el 2.1.6 mide, y que el panel de plan muestra."""
    t = db.create_table(esquema_alumnos(), quipudb.kind.HEAP)
    for c in range(1, 501):
        t.insert(alumno(c))
    ix = db.create_index("alumnos", "ix", "promedio", quipudb.kind.EXTENDIBLE_HASH)

    t.reset_stats()
    t.scan()
    paginas_scan = t.stats().pages_read

    ix.reset_stats()
    ix.search(15.0)
    paginas_indice = ix.stats().pages_read

    assert paginas_indice < paginas_scan, (
        f"el indice leyo {paginas_indice} paginas y el scan {paginas_scan}"
    )


def test_las_constantes_de_kind_son_las_del_core():
    """El plan de ejecucion (ADR 0002) y los benchmarks comparan contra estas
    cadenas exactas."""
    assert quipudb.kind.HEAP == "heap"
    assert quipudb.kind.SEQUENTIAL == "sequential"
    assert quipudb.kind.BPLUS_CLUSTERED == "bplus_clustered"
    assert quipudb.kind.BPLUS_UNCLUSTERED == "bplus_unclustered"
    assert quipudb.kind.EXTENDIBLE_HASH == "extendible_hash"


def test_las_medidas_del_2_1_6(db):
    t = db.create_table(esquema_alumnos(), quipudb.kind.SEQUENTIAL)
    for c in range(1, 201):
        t.insert(alumno(c))

    assert quipudb.file_size(t) > 0
    assert quipudb.wasted_ratio(t) == pytest.approx(0.0)

    for c in range(1, 101):
        t.remove(c)
    assert quipudb.wasted_ratio(t) > 0.0

    ms = quipudb.reorganize(t)
    assert ms >= 0.0
    assert quipudb.wasted_ratio(t) == pytest.approx(0.0)


def test_las_medidas_no_aplican_a_toda_organizacion(db):
    """Solo el archivo secuencial lleva cuenta del espacio desperdiciado."""
    t = db.create_table(esquema_alumnos(), quipudb.kind.HEAP)
    with pytest.raises(quipudb.Unsupported):
        quipudb.wasted_ratio(t)


# ---------------------------------------------------------------------------
# Ciclo de vida
# ---------------------------------------------------------------------------


def test_lo_escrito_sobrevive_al_cerrar_y_reabrir(tmp_path):
    ruta = tmp_path / "catalogo.txt"
    db = quipudb.Database(ruta)
    t = db.create_table(esquema_alumnos(), quipudb.kind.HEAP)
    for c in range(1, 51):
        t.insert(alumno(c))
    db.create_index("alumnos", "ix", "promedio", quipudb.kind.EXTENDIBLE_HASH)
    db.flush()
    del t, db

    otra = quipudb.Database(ruta)
    assert otra.has_table("alumnos")
    assert otra.table("alumnos").size() == 50
    assert otra.index("alumnos", "ix").size() == 50


def test_devuelve_siempre_el_mismo_objeto(db):
    """Dos handles sobre el mismo archivo se pisan al escribir."""
    a = db.create_table(esquema_alumnos(), quipudb.kind.HEAP)
    b = db.table("alumnos")
    assert a is b


def test_drop_table_se_lleva_los_indices(db):
    t = db.create_table(esquema_alumnos(), quipudb.kind.HEAP)
    t.insert(alumno(1))
    db.create_index("alumnos", "ix", "promedio", quipudb.kind.EXTENDIBLE_HASH)
    assert db.open_indexes() == ["alumnos.ix"]

    db.drop_table("alumnos")
    assert db.open_indexes() == []
    assert not db.has_table("alumnos")


def test_no_se_expone_el_cursor(db):
    """Un cursor deja de valer en cuanto la tabla se modifica. En C++ eso es una
    regla que se respeta; en Python seria un uso-despues-de-liberar dentro del
    interprete. Existe para el external sorting (#20), que es C++."""
    t = db.create_table(esquema_alumnos(), quipudb.kind.HEAP)
    assert not hasattr(t, "cursor")


def test_la_version_del_motor_es_legible():
    assert isinstance(quipudb.version(), str)
    assert quipudb.version()
