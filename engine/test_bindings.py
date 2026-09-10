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
    interprete.

    Lo que si se expone es `source_of(tabla)`, que lo envuelve: se puede pasar a
    un sort, un group by o un join, pero no guardar ni adelantar a mano."""
    t = db.create_table(esquema_alumnos(), quipudb.kind.HEAP)
    assert not hasattr(t, "cursor")
    assert hasattr(quipudb, "source_of")


def test_la_version_del_motor_es_legible():
    assert isinstance(quipudb.version(), str)
    assert quipudb.version()


# ---------------------------------------------------------------------------
# External algorithms (#20, #21, #22): lo que el #28 necesita
# ---------------------------------------------------------------------------


def esquema_notas() -> quipudb.Schema:
    return quipudb.Schema(
        table_name="notas",
        columns=[
            quipudb.Column("id", quipudb.DataType.INT),
            quipudb.Column("codigo", quipudb.DataType.INT),
            quipudb.Column("nota", quipudb.DataType.INT),
        ],
        key_column=0,
    )


def esquema_simple() -> quipudb.Schema:
    return quipudb.Schema(
        table_name="t",
        columns=[
            quipudb.Column("id", quipudb.DataType.INT),
            quipudb.Column("grupo", quipudb.DataType.INT),
        ],
        key_column=0,
    )


def test_un_flujo_se_recorre_con_un_for():
    filas = [[i, i % 3] for i in range(10)]
    assert list(quipudb.source_of(filas)) == filas


def test_un_flujo_no_se_recorre_dos_veces():
    """No es un vector disfrazado: se consume. Si alguien lo trata como lista,
    mejor que lo descubra aqui y no en el planner."""
    flujo = quipudb.source_of([[1, 1], [2, 2]])
    assert len(list(flujo)) == 2
    assert list(flujo) == []


def test_una_tabla_se_recorre_como_flujo_sin_materializarla(db):
    t = db.create_table(esquema_simple(), quipudb.kind.HEAP)
    for i in range(50):
        t.insert([i, i % 5])
    assert len(list(quipudb.source_of(t))) == 50


def test_el_sort_externo_ordena_y_usa_disco(tmp_path):
    """Buffers bajos a proposito: con los 64 por omision estas filas caben en
    memoria y `passes()` seria 0, o sea que no se probaria el k-way merge."""
    filas = [[i, (i * 7919) % 1000] for i in range(2000)]
    s = quipudb.ExternalSort(
        esquema_simple(), 1, buffers=4, page_size=512, dir=tmp_path
    )
    salida = list(s.sorted(quipudb.source_of(filas)))

    assert len(salida) == 2000
    assert [f[1] for f in salida] == sorted(f[1] for f in filas)
    assert s.passes() > 0, "no llego a tocar disco, la prueba no prueba el merge"
    assert s.stats().pages_written > 0


def test_el_sort_sin_disco_reporta_cero_pasadas(tmp_path):
    """El caso comun en tablas chicas, y lo que el plan de ejecucion muestra
    para distinguirlo."""
    s = quipudb.ExternalSort(esquema_simple(), 1, dir=tmp_path)
    assert len(list(s.sorted(quipudb.source_of([[i, -i] for i in range(10)])))) == 10
    assert s.passes() == 0
    assert s.stats().pages_written == 0


def test_el_group_by_agrega_y_escribe_particiones_a_disco(tmp_path):
    filas = [[i, i % 40] for i in range(2000)]
    g = quipudb.ExternalGroupBy(
        esquema_simple(),
        1,
        [quipudb.AggregateSpec.count(), quipudb.AggregateSpec.of(quipudb.Aggregate.SUM, 0)],
        quipudb.ExternalGroupBy.Strategy.HASH,
        buffers=4,
        page_size=512,
        dir=tmp_path,
    )
    salida = list(g.grouped(quipudb.source_of(filas)))

    assert len(salida) == 40
    assert g.used() == quipudb.ExternalGroupBy.Strategy.HASH
    assert sum(f[1] for f in salida) == 2000
    assert sum(f[2] for f in salida) == sum(range(2000))
    # Si esto fuera 0 el hash no seria externo, que es el bug que se arreglo
    # en el #21 despues de mergearlo.
    assert g.stats().pages_written > 0


def test_el_esquema_de_salida_del_group_by_no_es_el_de_la_entrada(tmp_path):
    g = quipudb.ExternalGroupBy(
        esquema_simple(),
        1,
        [quipudb.AggregateSpec.count(), quipudb.AggregateSpec.of(quipudb.Aggregate.AVG, 0)],
        dir=tmp_path,
    )
    nombres = [c.name for c in g.output_schema().columns]
    assert nombres == ["grupo", "COUNT_all", "AVG_id"]


def test_los_dos_caminos_del_group_by_dan_lo_mismo(tmp_path):
    """Si hash y sort no coinciden, comparar sus tiempos en el 2.1.6 no
    significaria nada."""
    filas = [[i, i % 17] for i in range(1200)]
    resultados = []
    for estrategia in (
        quipudb.ExternalGroupBy.Strategy.HASH,
        quipudb.ExternalGroupBy.Strategy.SORT,
    ):
        g = quipudb.ExternalGroupBy(
            esquema_simple(),
            1,
            [quipudb.AggregateSpec.count()],
            estrategia,
            buffers=4,
            page_size=512,
            dir=tmp_path,
        )
        resultados.append(sorted(g.grouped(quipudb.source_of(filas))))
    assert resultados[0] == resultados[1]


def test_el_join_por_hash_coincide_con_un_join_en_memoria(tmp_path):
    izq = [[i, i % 100] for i in range(300)]
    der = [[i, i % 100, i % 21] for i in range(900)]

    j = quipudb.ExternalJoin(
        esquema_simple(),
        1,
        esquema_notas(),
        1,
        quipudb.ExternalJoin.Strategy.HASH,
        buffers=6,
        page_size=512,
        dir=tmp_path,
    )
    obtenido = sorted(j.joined(quipudb.source_of(izq), quipudb.source_of(der)))

    esperado = sorted(i + d for i in izq for d in der if i[1] == d[1])
    assert obtenido == esperado
    assert j.structure() == "external_hash"
    assert j.stats().pages_written > 0


def test_el_esquema_del_join_desambigua_solo_las_columnas_repetidas(tmp_path):
    j = quipudb.ExternalJoin(esquema_simple(), 1, esquema_notas(), 1, dir=tmp_path)
    nombres = [c.name for c in j.output_schema().columns]
    # `id` esta en los dos lados y se prefija; `grupo`, `codigo` y `nota` no.
    assert nombres == ["t.id", "grupo", "notas.id", "codigo", "nota"]


def test_el_join_por_indice_da_lo_mismo_que_por_hash(db, tmp_path):
    izq = [[i, i % 100] for i in range(200)]
    der = [[i, i % 100, i % 21] for i in range(600)]

    t = db.create_table(esquema_notas(), quipudb.kind.HEAP)
    for f in der:
        t.insert(f)
    ix = db.create_index("notas", "por_codigo", "codigo", quipudb.kind.BPLUS_UNCLUSTERED)
    sonda = quipudb.probe_of(ix, t)
    assert sonda.structure() == quipudb.kind.BPLUS_UNCLUSTERED

    por_indice = quipudb.ExternalJoin(
        esquema_simple(), 1, esquema_notas(), 1,
        quipudb.ExternalJoin.Strategy.INDEX_NESTED, buffers=6, page_size=512, dir=tmp_path,
    )
    a = sorted(
        por_indice.joined(quipudb.source_of(izq), sonda, quipudb.source_of(der), len(izq))
    )

    por_hash = quipudb.ExternalJoin(
        esquema_simple(), 1, esquema_notas(), 1,
        quipudb.ExternalJoin.Strategy.HASH, buffers=6, page_size=512, dir=tmp_path,
    )
    b = sorted(por_hash.joined(quipudb.source_of(izq), quipudb.source_of(der)))

    assert a == b
    assert por_indice.used() == quipudb.ExternalJoin.Strategy.INDEX_NESTED
    assert por_indice.structure() == quipudb.kind.BPLUS_UNCLUSTERED
    # El INL no particiona: si escribio algo, no es un INL.
    assert por_indice.stats().pages_written == 0


def test_auto_elige_hash_con_dos_lados_grandes(db, tmp_path):
    """La regla medida: el INL cuesta por FILA externa y el hash por PAGINA, asi
    que con los dos lados grandes gana el hash aunque haya indice. Es justo el
    caso donde el criterio original del #22 se equivocaba 85 veces."""
    der = [[i, i % 500, i % 21] for i in range(1000)]
    t = db.create_table(esquema_notas(), quipudb.kind.HEAP)
    for f in der:
        t.insert(f)
    ix = db.create_index("notas", "por_codigo", "codigo", quipudb.kind.BPLUS_UNCLUSTERED)
    sonda = quipudb.probe_of(ix, t)

    izq = [[i, i % 500] for i in range(1000)]
    j = quipudb.ExternalJoin(
        esquema_simple(), 1, esquema_notas(), 1,
        quipudb.ExternalJoin.Strategy.AUTO, buffers=6, page_size=512, dir=tmp_path,
    )
    list(j.joined(quipudb.source_of(izq), sonda, quipudb.source_of(der), len(izq)))
    assert j.used() == quipudb.ExternalJoin.Strategy.HASH


def test_auto_sin_saber_el_tamano_externo_elige_hash(db, tmp_path):
    """Un 0 significa 'no lo se'. Equivocarse hacia hash cuesta un factor dos;
    hacia INL, hasta 2180x."""
    der = [[i, i % 50, i % 21] for i in range(200)]
    t = db.create_table(esquema_notas(), quipudb.kind.HEAP)
    for f in der:
        t.insert(f)
    ix = db.create_index("notas", "por_codigo", "codigo", quipudb.kind.BPLUS_UNCLUSTERED)
    sonda = quipudb.probe_of(ix, t)

    izq = [[i, i % 50] for i in range(5)]
    j = quipudb.ExternalJoin(
        esquema_simple(), 1, esquema_notas(), 1,
        quipudb.ExternalJoin.Strategy.AUTO, buffers=6, page_size=512, dir=tmp_path,
    )
    list(j.joined(quipudb.source_of(izq), sonda, quipudb.source_of(der), 0))
    assert j.used() == quipudb.ExternalJoin.Strategy.HASH


def test_la_regla_de_eleccion_se_puede_consultar_sin_correr_el_join():
    """El planner tiene que poder explicar por que eligio, no solo elegir."""
    # Lado externo diminuto contra interno grande: gana el INL.
    assert quipudb.ExternalJoin.conviene_index_nested(50, 1, 124, 4)
    # Dos lados de 10 000: gana el hash, que es lo que la medicion mostro.
    assert not quipudb.ExternalJoin.conviene_index_nested(10000, 124, 124, 4)


def test_el_indice_nested_loop_exige_una_sonda(tmp_path):
    j = quipudb.ExternalJoin(
        esquema_simple(), 1, esquema_notas(), 1,
        quipudb.ExternalJoin.Strategy.INDEX_NESTED, dir=tmp_path,
    )
    with pytest.raises(quipudb.Unsupported):
        j.joined(quipudb.source_of([[1, 1]]), quipudb.source_of([[1, 1, 1]]))


# ---------------------------------------------------------------------------
# Tiempos de vida: lo que se caeria sin keep_alive
# ---------------------------------------------------------------------------
#
# Los tres objetos son dueños de sus temporales, y su cabecera dice que el flujo
# que devuelven deja de valer si el objeto muere. En C++ eso es una regla que se
# lee y se respeta; en Python, donde el recolector decide cuando destruir, seria
# un uso-despues-de-liberar dentro del interprete. Estas pruebas son el patron
# exacto que se caeria.
#
# QUE PASA DE VERDAD SIN `py::keep_alive`, medido quitandolos de `module.cpp` y
# corriendo cada prueba por separado:
#
#   sort       PASA.     `MergeSource` guarda `shared_ptr<Temporal>`, asi que
#                        los archivos sobreviven al ExternalSort; y el camino en
#                        memoria es dueño de su vector.
#   group by   PASA.     `Salida` materializa el resultado, asi que el flujo no
#                        referencia nada del ExternalGroupBy.
#   INL        REVIENTA. Segmentation fault, exit 139. `SalidaIndexNested`
#                        guarda punteros CRUDOS al join, al flujo izquierdo y a
#                        la sonda, y los usa mientras se lee.
#
# O sea que hoy solo el INL depende de esto. Los `keep_alive` se quedan en los
# tres igual: lo que los otros dos tienen no es una garantia del contrato sino
# una casualidad de como estan implementados hoy, y el propio #21 dice que la
# salida del group by deberia dejar de materializarse. El dia que eso pase, esta
# prueba ya estaria puesta.


def test_el_flujo_del_sort_sobrevive_al_sort(tmp_path):
    def hacer():
        s = quipudb.ExternalSort(esquema_simple(), 1, buffers=4, page_size=512, dir=tmp_path)
        return s.sorted(quipudb.source_of([[i, -i] for i in range(2000)]))

    import gc

    flujo = hacer()
    gc.collect()  # el ExternalSort y la fuente ya no tienen referencias visibles
    assert len(list(flujo)) == 2000


def test_el_flujo_del_group_by_sobrevive_al_group_by(tmp_path):
    def hacer():
        g = quipudb.ExternalGroupBy(
            esquema_simple(), 1, [quipudb.AggregateSpec.count()],
            quipudb.ExternalGroupBy.Strategy.HASH, buffers=4, page_size=512, dir=tmp_path,
        )
        return g.grouped(quipudb.source_of([[i, i % 30] for i in range(2000)]))

    import gc

    flujo = hacer()
    gc.collect()
    assert len(list(flujo)) == 30


def test_el_flujo_del_index_nested_loop_sobrevive_a_todo(db, tmp_path):
    """El caso peor: la salida del INL recorre el flujo externo y sondea el
    indice MIENTRAS se lee, asi que guarda punteros al join, a la fuente
    izquierda y a la sonda. Los tres tienen que sobrevivir."""
    der = [[i, i % 40, i % 21] for i in range(400)]
    t = db.create_table(esquema_notas(), quipudb.kind.HEAP)
    for f in der:
        t.insert(f)
    db.create_index("notas", "por_codigo", "codigo", quipudb.kind.BPLUS_UNCLUSTERED)

    def hacer():
        ix = db.index("notas", "por_codigo")
        sonda = quipudb.probe_of(ix, t)
        j = quipudb.ExternalJoin(
            esquema_simple(), 1, esquema_notas(), 1,
            quipudb.ExternalJoin.Strategy.INDEX_NESTED, buffers=6, page_size=512, dir=tmp_path,
        )
        izq = [[i, i % 40] for i in range(50)]
        return j.joined(quipudb.source_of(izq), sonda, quipudb.source_of(der), len(izq))

    import gc

    flujo = hacer()
    gc.collect()
    assert len(list(flujo)) == 50 * 10


# ---------------------------------------------------------------------------
# De punta a punta: lo que el #28 va a hacer de verdad
# ---------------------------------------------------------------------------


def test_order_by_group_by_y_join_sobre_tablas_reales(db, tmp_path):
    """El recorrido completo desde tablas del catalogo, que es como lo va a usar
    el parser: nada de listas en memoria fabricadas para la prueba.

    Buffers bajos a proposito: con los 64 por omision esto cabria en memoria y
    no se estaria probando ningun external algorithm."""
    alumnos = quipudb.Schema(
        table_name="alumnos",
        columns=[
            quipudb.Column("codigo", quipudb.DataType.INT),
            quipudb.Column("ciclo", quipudb.DataType.INT),
        ],
        key_column=0,
    )
    ta = db.create_table(alumnos, quipudb.kind.HEAP)
    for i in range(600):
        ta.insert([i, i % 10])
    tn = db.create_table(esquema_notas(), quipudb.kind.HEAP)
    for i in range(1200):
        tn.insert([i, i % 600, i % 21])

    # ORDER BY ciclo
    s = quipudb.ExternalSort(alumnos, 1, buffers=4, page_size=512, dir=tmp_path)
    ordenado = list(s.sorted(quipudb.source_of(ta)))
    assert len(ordenado) == 600
    assert [f[1] for f in ordenado] == sorted(f[1] for f in ordenado)
    assert s.passes() > 0

    # GROUP BY ciclo
    g = quipudb.ExternalGroupBy(
        alumnos,
        1,
        [quipudb.AggregateSpec.count()],
        quipudb.ExternalGroupBy.Strategy.HASH,
        buffers=4,
        page_size=512,
        dir=tmp_path,
    )
    grupos = list(g.grouped(quipudb.source_of(ta)))
    assert len(grupos) == 10
    assert all(f[1] == 60 for f in grupos)
    assert g.stats().pages_written > 0

    # JOIN alumnos.codigo = notas.codigo
    j = quipudb.ExternalJoin(
        alumnos, 0, esquema_notas(), 1, quipudb.ExternalJoin.Strategy.HASH,
        buffers=6, page_size=512, dir=tmp_path,
    )
    filas = list(j.joined(quipudb.source_of(ta), quipudb.source_of(tn)))
    assert len(filas) == 1200
    assert j.output_rows() == 1200
    assert j.stats().pages_written > 0
    # El esquema de salida es lo que el frontend (#37) pone de cabecera.
    # `codigo` esta en LOS DOS lados, asi que se prefijan las dos, no solo una;
    # `ciclo` y `nota` no colisionan y conservan su nombre. `id` tampoco
    # colisiona aqui, porque el lado izquierdo no tiene ninguna columna `id`.
    assert [c.name for c in j.output_schema().columns] == [
        "alumnos.codigo",
        "ciclo",
        "id",
        "notas.codigo",
        "nota",
    ]
