"""Ida y vuelta de una columna VECTOR a traves de los bindings (issue #180).

Estas pruebas existen por lo que encontraron: tres defectos que ninguna
prueba del parser ni del codec podia ver, porque solo aparecen cuando un
registro con vector recorre el camino completo -- crear la tabla, insertar,
cerrar, reabrir desde el catalogo en disco y volver a leer.

1. El catalogo rechazaba cualquier columna que no fuera VARCHAR con un numero
   entre parentesis, asi que `CREATE TABLE ... VECTOR(4)` no llegaba ni a
   crearse.
2. `parse_type` no conocia VECTOR, asi que una tabla con esa columna no se
   podia reabrir.
3. El catalogo escribia el numero solo para VARCHAR, asi que la dimension no
   sobrevivia al disco y la tabla volvia con registros de otro tamano.

Se saltan enteras si el modulo no esta compilado; el CI SI lo compila.
"""

from __future__ import annotations

import math

import pytest

quipudb = pytest.importorskip(
    "quipudb_native",
    reason="los bindings no estan compilados: cmake -DQUIPUDB_BUILD_PYTHON=ON",
)

DIMENSION = 4
RASGOS = [0.5, -1.25, 0.0, 3.5]


def esquema_fotos(dimension: int = DIMENSION) -> quipudb.Schema:
    """Metadatos y el vector juntos, que es lo que pide el criterio 4."""
    return quipudb.Schema(
        table_name="fotos",
        columns=[
            quipudb.Column("id", quipudb.DataType.INT),
            quipudb.Column("ruta", quipudb.DataType.VARCHAR, 16),
            quipudb.Column("rasgos", quipudb.DataType.VECTOR, dimension),
        ],
        key_column=0,
    )


@pytest.fixture()
def tabla(tmp_path):
    db = quipudb.Database(tmp_path / "catalogo.txt")
    return db, db.create_table(esquema_fotos(), quipudb.kind.HEAP)


# ---------------------------------------------------------------------------
# Crear, insertar y leer
# ---------------------------------------------------------------------------


def test_se_puede_crear_una_tabla_con_columna_vectorial(tabla) -> None:
    _, t = tabla
    assert t.size() == 0


def test_un_vector_vuelve_con_sus_componentes_exactas(tabla) -> None:
    _, t = tabla
    t.insert([1, "a.jpg", RASGOS])
    assert next(iter(t.scan())) == [1, "a.jpg", RASGOS]


def test_una_tupla_tambien_sirve_como_vector(tabla) -> None:
    # El caster acepta list y tuple; una cadena no, que ya es VARCHAR.
    _, t = tabla
    t.insert([1, "a.jpg", tuple(RASGOS)])
    assert next(iter(t.scan()))[2] == RASGOS


def test_el_vector_llega_a_python_como_lista(tabla) -> None:
    _, t = tabla
    t.insert([1, "a.jpg", RASGOS])
    assert isinstance(next(iter(t.scan()))[2], list)


def test_los_enteros_de_la_lista_se_leen_como_componentes(tabla) -> None:
    _, t = tabla
    t.insert([1, "a.jpg", [1, 2, 3, 4]])
    assert next(iter(t.scan()))[2] == [1.0, 2.0, 3.0, 4.0]


# ---------------------------------------------------------------------------
# El vector sobrevive al disco
# ---------------------------------------------------------------------------


def test_la_tabla_con_vector_se_puede_reabrir(tmp_path) -> None:
    ruta = tmp_path / "catalogo.txt"
    db = quipudb.Database(ruta)
    t = db.create_table(esquema_fotos(), quipudb.kind.HEAP)
    t.insert([1, "a.jpg", RASGOS])
    db.flush()
    del t, db

    otra = quipudb.Database(ruta)
    assert next(iter(otra.table("fotos").scan())) == [1, "a.jpg", RASGOS]


def test_la_dimension_queda_escrita_en_el_catalogo(tmp_path) -> None:
    # Sin esto la tabla volveria con dimension 0 y registros de otro tamano
    # que el archivo que ya existe en disco.
    ruta = tmp_path / "catalogo.txt"
    db = quipudb.Database(ruta)
    db.create_table(esquema_fotos(7), quipudb.kind.HEAP)
    db.flush()
    assert "column rasgos VECTOR 7" in ruta.read_text(encoding="utf-8")


def test_la_dimension_sobrevive_a_la_reapertura(tmp_path) -> None:
    ruta = tmp_path / "catalogo.txt"
    db = quipudb.Database(ruta)
    db.create_table(esquema_fotos(7), quipudb.kind.HEAP)
    db.flush()
    del db

    columna = quipudb.Database(ruta).table_info("fotos").schema.columns[2]
    assert columna.type == quipudb.DataType.VECTOR
    assert columna.length == 7


# ---------------------------------------------------------------------------
# Lo que no entra
# ---------------------------------------------------------------------------


def test_un_vector_de_otra_dimension_no_entra(tabla) -> None:
    _, t = tabla
    with pytest.raises(quipudb.InvalidRecord, match="componentes"):
        t.insert([1, "a.jpg", [1.0, 2.0]])


def test_un_vector_mas_largo_tampoco_entra(tabla) -> None:
    _, t = tabla
    with pytest.raises(quipudb.InvalidRecord, match="componentes"):
        t.insert([1, "a.jpg", [1.0, 2.0, 3.0, 4.0, 5.0]])


def test_una_componente_no_finita_no_entra(tabla) -> None:
    # Validar aqui, al insertar, es lo que permite que las metricas de #179
    # no tengan que revalidar el vector en cada comparacion.
    _, t = tabla
    with pytest.raises(quipudb.InvalidRecord, match="no es finita"):
        t.insert([1, "a.jpg", [1.0, math.nan, 3.0, 4.0]])


def test_un_infinito_tampoco_entra(tabla) -> None:
    _, t = tabla
    with pytest.raises(quipudb.InvalidRecord, match="no es finita"):
        t.insert([1, "a.jpg", [1.0, math.inf, 3.0, 4.0]])


def test_una_columna_vectorial_sin_dimension_se_rechaza(tmp_path) -> None:
    db = quipudb.Database(tmp_path / "catalogo.txt")
    with pytest.raises(quipudb.SchemaError, match="VECTOR"):
        db.create_table(esquema_fotos(0), quipudb.kind.HEAP)


def test_los_tipos_sin_parentesis_siguen_sin_aceptar_un_numero(tmp_path) -> None:
    db = quipudb.Database(tmp_path / "catalogo.txt")
    esquema = quipudb.Schema(
        table_name="t",
        columns=[quipudb.Column("id", quipudb.DataType.INT, 5)],
        key_column=0,
    )
    with pytest.raises(quipudb.SchemaError, match="VARCHAR y VECTOR"):
        db.create_table(esquema, quipudb.kind.HEAP)
