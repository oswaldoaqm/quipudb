"""El esquema de Rikuq y su presupuesto de bytes (#302)."""

from app.carga.esquema import COLUMNAS, PERSONAS, ROSTROS, crear_tabla
from app.rostros.embedding import DIMENSIONES


def test_los_metadatos_y_el_vector_van_en_tablas_distintas() -> None:
    """Repetir el nombre en cada rostro seria guardarlo 530 veces para Bush."""

    nombres = {c.nombre for c in COLUMNAS[PERSONAS]}
    de_rostros = {c.nombre for c in COLUMNAS[ROSTROS]}

    assert "nombre" in nombres and "nombre" not in de_rostros
    assert "embedding" in de_rostros and "embedding" not in nombres


def test_cada_tabla_tiene_una_sola_clave_primaria() -> None:
    for tabla in (PERSONAS, ROSTROS):
        assert sum(1 for c in COLUMNAS[tabla] if c.clave) == 1, tabla


def test_los_rostros_apuntan_a_una_persona() -> None:
    assert "persona_id" in {c.nombre for c in COLUMNAS[ROSTROS]}


def test_crear_tabla_sale_en_heap() -> None:
    """MATCH y los indices secundarios solo funcionan sobre heap."""

    for tabla in (PERSONAS, ROSTROS):
        assert crear_tabla(tabla).endswith("USING HEAP")


def test_crear_tabla_declara_la_dimension_del_vector() -> None:
    assert f"embedding VECTOR({DIMENSIONES})" in crear_tabla(ROSTROS)


def test_crear_tabla_marca_la_clave_primaria() -> None:
    assert "id INT PRIMARY KEY" in crear_tabla(PERSONAS)
