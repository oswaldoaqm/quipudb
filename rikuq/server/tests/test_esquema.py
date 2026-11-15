"""El esquema de Rikuq y su presupuesto de bytes (#302)."""

import re

from app.carga.esquema import (
    BYTES_INT,
    BYTES_POR_COMPONENTE,
    COLUMNAS,
    INDICES,
    MAX_BYTES_HEAP,
    PERSONAS,
    ROSTROS,
    Columna,
    bytes_por_registro,
    crear_tabla,
    sentencias,
)
from app.rostros.embedding import DIMENSIONES


def test_el_maximo_del_heap_es_el_del_motor() -> None:
    """4096 de pagina, menos 8 de cabecera y 1 de estado de slot."""

    assert MAX_BYTES_HEAP == 4096 - 8 - 1


def test_un_vector_de_512_ocupa_2048_bytes() -> None:
    """Cada componente es un float de 32 bits."""

    assert Columna("embedding", "VECTOR", DIMENSIONES).bytes == DIMENSIONES * BYTES_POR_COMPONENTE
    assert Columna("embedding", "VECTOR", DIMENSIONES).bytes == 2048


def test_un_varchar_ocupa_su_largo_y_un_int_cuatro() -> None:
    assert Columna("nombre", "VARCHAR", 100).bytes == 100
    assert Columna("id", "INT").bytes == BYTES_INT


def test_las_dos_tablas_caben_en_un_registro_de_heap() -> None:
    """Es el criterio del issue: justificar que entra en 4087 bytes."""

    for tabla in (PERSONAS, ROSTROS):
        assert bytes_por_registro(tabla) < MAX_BYTES_HEAP, tabla


def test_el_registro_de_rostros_son_2256_bytes() -> None:
    """4 + 4 + 200 + 2048. Sigue entrando en los 4087 de una pagina."""

    assert bytes_por_registro(ROSTROS) == 2256


def test_el_registro_de_personas_son_118_bytes() -> None:
    assert bytes_por_registro(PERSONAS) == 118


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


def test_el_indice_del_embedding_es_hnsw_con_coseno() -> None:
    """HNSW se crea vacio; IVF necesitaria datos para entrenar sus centroides."""

    hnsw = [sql for sql in INDICES if "HNSW" in sql]

    assert len(hnsw) == 1
    assert "embedding" in hnsw[0]
    assert "METRIC=cosine" in hnsw[0]


def test_hay_un_indice_para_traer_los_rostros_de_una_persona() -> None:
    assert any("persona_id" in sql and "HASH" in sql for sql in INDICES)


def test_las_tablas_se_crean_antes_que_sus_indices() -> None:
    """Un CREATE INDEX sobre una tabla que no existe falla."""

    orden = sentencias()
    tablas = [i for i, sql in enumerate(orden) if sql.startswith("CREATE TABLE")]
    indices = [i for i, sql in enumerate(orden) if sql.startswith("CREATE INDEX")]

    assert max(tablas) < min(indices)


def test_ninguna_sentencia_termina_en_punto_y_coma() -> None:
    """Se unen con ';' al mandarlas en lote; uno de mas rompe el lote."""

    for sql in sentencias():
        assert not sql.rstrip().endswith(";")


def test_los_nombres_de_tabla_e_indice_son_identificadores_validos() -> None:
    """El motor limita los identificadores a 64 caracteres."""

    for sql in sentencias():
        for nombre in re.findall(r"CREATE (?:TABLE|INDEX) (\w+)", sql):
            assert len(nombre) <= 64
            assert re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", nombre)
