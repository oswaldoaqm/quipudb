"""Pruebas de la conversion de la carga CSV (issue #114). No necesitan el core."""

from __future__ import annotations

import io
from datetime import date

import pytest

from engine.executor.bulk_load import (
    MAX_REPORTED_ERRORS,
    CsvLoadError,
    LoadReport,
    check_utf8,
    convert_field,
    convert_row,
    detect_encoding,
    match_header,
    read_header,
)
from engine.parser.ast import SqlTypeName
from engine.parser.bound_ast import BoundColumn, BoundSchema

ESQUEMA = BoundSchema(
    table_name="alumnos",
    columns=(
        BoundColumn("codigo", SqlTypeName.INT, None),
        BoundColumn("nombre", SqlTypeName.VARCHAR, 8),
        BoundColumn("promedio", SqlTypeName.DOUBLE, None),
    ),
    key_column=0,
)


def _col(tipo: SqlTypeName, largo: int | None = None) -> BoundColumn:
    return BoundColumn("c", tipo, largo)


# ---------------------------------------------------------------------------
# Cabecera
# ---------------------------------------------------------------------------


def test_la_cabecera_se_empareja_en_cualquier_orden() -> None:
    assert match_header(["promedio", "codigo", "nombre"], ESQUEMA) == (1, 2, 0)
    assert match_header(["codigo", "nombre", "promedio"], ESQUEMA) == (0, 1, 2)


def test_los_espacios_alrededor_de_los_nombres_no_importan() -> None:
    assert match_header([" codigo", "nombre ", " promedio "], ESQUEMA) == (0, 1, 2)


def test_una_columna_que_falta_se_nombra() -> None:
    with pytest.raises(CsvLoadError, match="faltan 'promedio'") as error:
        match_header(["codigo", "nombre"], ESQUEMA)
    assert error.value.line == 1


def test_una_columna_que_sobra_se_nombra() -> None:
    with pytest.raises(CsvLoadError, match="sobran 'edad'"):
        match_header(["codigo", "nombre", "promedio", "edad"], ESQUEMA)


def test_faltan_y_sobran_a_la_vez_se_dicen_las_dos_cosas() -> None:
    with pytest.raises(CsvLoadError) as error:
        match_header(["codigo", "nombres", "promedio"], ESQUEMA)
    mensaje = error.value.message
    assert "faltan 'nombre'" in mensaje
    assert "sobran 'nombres'" in mensaje
    assert "La tabla tiene 'codigo', 'nombre', 'promedio'" in mensaje


def test_una_columna_repetida_se_rechaza() -> None:
    with pytest.raises(CsvLoadError, match="se repite 'codigo'"):
        match_header(["codigo", "nombre", "promedio", "codigo"], ESQUEMA)


def test_las_mayusculas_de_la_cabecera_no_importan() -> None:
    assert match_header(["CODIGO", "Nombre", "Promedio"], ESQUEMA) == (0, 1, 2)


def test_si_dos_columnas_solo_difieren_en_mayusculas_se_exige_el_nombre_exacto() -> None:
    esquema = BoundSchema(
        table_name="t",
        columns=(
            BoundColumn("id", SqlTypeName.INT, None),
            BoundColumn("ID", SqlTypeName.INT, None),
        ),
        key_column=0,
    )
    assert match_header(["ID", "id"], esquema) == (1, 0)
    with pytest.raises(CsvLoadError, match="sobran 'Id'"):
        match_header(["Id", "id"], esquema)


def test_la_misma_columna_con_otras_mayusculas_cuenta_como_repetida() -> None:
    with pytest.raises(CsvLoadError, match="se repite 'codigo'"):
        match_header(["codigo", "CODIGO", "nombre", "promedio"], ESQUEMA)


def test_las_columnas_sin_nombre_del_final_se_ignoran() -> None:
    assert match_header(["codigo", "nombre", "promedio", "", " "], ESQUEMA) == (0, 1, 2)


def test_una_columna_sin_nombre_en_medio_se_rechaza() -> None:
    with pytest.raises(CsvLoadError, match="la columna 2 de la cabecera no tiene nombre"):
        match_header(["codigo", "", "nombre", "promedio"], ESQUEMA)


def test_el_error_de_cabecera_lleva_su_linea() -> None:
    with pytest.raises(CsvLoadError) as error:
        match_header(["codigo"], ESQUEMA, line=3)
    assert error.value.line == 3


def test_una_cabecera_vacia_se_rechaza() -> None:
    with pytest.raises(CsvLoadError, match="no tiene cabecera"):
        match_header([""], ESQUEMA)


# ---------------------------------------------------------------------------
# Filas
# ---------------------------------------------------------------------------


def test_una_fila_se_reordena_y_se_convierte() -> None:
    mapping = match_header(["promedio", "codigo", "nombre"], ESQUEMA)
    assert convert_row(["15.5", "7", "ana"], mapping, ESQUEMA, 3) == [7, "ana", 15.5]


def test_un_valor_en_una_columna_sin_nombre_no_se_pierde_en_silencio() -> None:
    mapping = (0, 1, 2)
    assert convert_row(["1", "a", "2", ""], mapping, ESQUEMA, 4) == [1, "a", 2.0]
    with pytest.raises(ValueError, match="el campo 4 trae 'x'"):
        convert_row(["1", "a", "2", "x"], mapping, ESQUEMA, 4)


def test_una_fila_con_otro_numero_de_campos_se_rechaza() -> None:
    mapping = (0, 1, 2)
    with pytest.raises(ValueError, match="tiene 4 campos y la cabecera tiene 3"):
        convert_row(["1", "a", "2.0", "x"], mapping, ESQUEMA, 3)
    with pytest.raises(ValueError, match="tiene 2 campos"):
        convert_row(["1", "a"], mapping, ESQUEMA, 3)


# ---------------------------------------------------------------------------
# Conversion por tipo
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [("42", 42), (" -7 ", -7), ("+3", 3), ("2147483647", 2**31 - 1), ("-2147483648", -(2**31))],
)
def test_int_valido(texto: str, esperado: int) -> None:
    assert convert_field(texto, _col(SqlTypeName.INT)) == esperado


@pytest.mark.parametrize("texto", ["4.0", "1e3", "abc", "1_000", "٣"])
def test_int_invalido(texto: str) -> None:
    with pytest.raises(ValueError, match="no es un INT"):
        convert_field(texto, _col(SqlTypeName.INT))


def test_int_fuera_de_rango() -> None:
    with pytest.raises(ValueError, match="fuera del rango INT de 32 bits"):
        convert_field("2147483648", _col(SqlTypeName.INT))


@pytest.mark.parametrize(("texto", "esperado"), [("15.5", 15.5), ("3", 3.0), ("-1e2", -100.0)])
def test_double_valido(texto: str, esperado: float) -> None:
    assert convert_field(texto, _col(SqlTypeName.DOUBLE)) == esperado


@pytest.mark.parametrize("texto", ["nan", "inf", "-Infinity", "1e999"])
def test_double_no_finito(texto: str) -> None:
    with pytest.raises(ValueError, match="valor finito"):
        convert_field(texto, _col(SqlTypeName.DOUBLE))


def test_double_con_coma_decimal_solo_si_el_csv_la_usa() -> None:
    columna = _col(SqlTypeName.DOUBLE)
    assert convert_field("15,5", columna, decimal_comma=True) == 15.5
    assert convert_field("15.5", columna, decimal_comma=True) == 15.5
    with pytest.raises(ValueError, match="no es un DOUBLE"):
        convert_field("15,5", columna)
    # Separador de miles: ambiguo, no se adivina.
    with pytest.raises(ValueError, match="no es un DOUBLE"):
        convert_field("1.234,5", columna, decimal_comma=True)


def test_double_que_no_es_numero() -> None:
    with pytest.raises(ValueError, match="no es un DOUBLE"):
        convert_field("quince", _col(SqlTypeName.DOUBLE))


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [("true", True), ("TRUE", True), ("1", True), ("false", False), ("False", False), ("0", False)],
)
def test_bool(texto: str, esperado: bool) -> None:
    assert convert_field(texto, _col(SqlTypeName.BOOL)) is esperado


def test_bool_invalido() -> None:
    with pytest.raises(ValueError, match="no es un BOOL"):
        convert_field("si", _col(SqlTypeName.BOOL))


def test_date() -> None:
    assert convert_field("2026-10-03", _col(SqlTypeName.DATE)) == date(2026, 10, 3)


@pytest.mark.parametrize(
    ("texto", "motivo"),
    [
        ("20261003", "AAAA-MM-DD"),
        ("03/10/2026", "AAAA-MM-DD"),
        ("2026-02-30", "no es una fecha valida"),
    ],
)
def test_date_invalida(texto: str, motivo: str) -> None:
    with pytest.raises(ValueError, match=motivo):
        convert_field(texto, _col(SqlTypeName.DATE))


def test_varchar_se_guarda_tal_cual_con_espacios() -> None:
    assert convert_field("  ana ", _col(SqlTypeName.VARCHAR, 8)) == "  ana "
    assert convert_field("", _col(SqlTypeName.VARCHAR, 8)) == ""


def test_varchar_mide_en_bytes_utf8() -> None:
    columna = _col(SqlTypeName.VARCHAR, 4)
    assert convert_field("ñaño", _col(SqlTypeName.VARCHAR, 6)) == "ñaño"
    with pytest.raises(ValueError, match="ocupa 6 bytes y supera VARCHAR\\(4\\)"):
        convert_field("ñaño", columna)


def test_varchar_rechaza_bytes_nulos() -> None:
    with pytest.raises(ValueError, match="bytes nulos"):
        convert_field("a\0b", _col(SqlTypeName.VARCHAR, 8))


@pytest.mark.parametrize(
    "tipo", [SqlTypeName.INT, SqlTypeName.DOUBLE, SqlTypeName.BOOL, SqlTypeName.DATE]
)
def test_un_campo_vacio_solo_vale_en_varchar(tipo: SqlTypeName) -> None:
    with pytest.raises(ValueError, match="esta vacia"):
        convert_field("  ", _col(tipo))


# ---------------------------------------------------------------------------
# Reporte
# ---------------------------------------------------------------------------


def test_el_reporte_cuenta_todo_y_detalla_solo_las_primeras() -> None:
    reporte = LoadReport("t")
    for linea in range(2, MAX_REPORTED_ERRORS + 12):
        reporte.fail(linea, "mal")
    assert reporte.failed == MAX_REPORTED_ERRORS + 10
    assert len(reporte.errors) == MAX_REPORTED_ERRORS
    assert reporte.errors[0].line == 2
    assert reporte.errors_truncated


def test_un_reporte_sin_fallas_no_esta_truncado() -> None:
    assert not LoadReport("t").errors_truncated


# ---------------------------------------------------------------------------
# UTF-8
# ---------------------------------------------------------------------------


def test_check_utf8_deja_el_archivo_al_inicio() -> None:
    archivo = io.BytesIO("codigo\nñandú\n".encode())
    check_utf8(archivo)
    assert archivo.tell() == 0


def test_check_utf8_cuenta_bien_la_linea_entre_bloques() -> None:
    # Bloques de 5 bytes: la "ñ" (2 bytes) queda partida entre dos bloques y no
    # es un error; el 0xff de la linea 4 si.
    contenido = b"ab\ncd\n\xc3\xb1\nx\xffy\n"
    with pytest.raises(CsvLoadError) as error:
        check_utf8(io.BytesIO(contenido), chunk_size=5)
    assert error.value.line == 4


def test_check_utf8_detecta_un_caracter_cortado_al_final() -> None:
    with pytest.raises(CsvLoadError, match="a mitad de un caracter"):
        check_utf8(io.BytesIO(b"abc\n\xc3"))


# ---------------------------------------------------------------------------
# Lectura de la cabecera
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("texto", "separador"),
    [
        ("codigo,nombre,promedio\n", ","),
        ("codigo;nombre;promedio\n", ";"),
        ("codigo\tnombre\tpromedio\n", "\t"),
        ('"codigo","nombre","promedio"\n', ","),
        ('"codigo";"nombre";"promedio"\n', ";"),
        ("codigo\n", ","),
    ],
)
def test_el_separador_se_detecta_en_la_cabecera(texto: str, separador: str) -> None:
    cabecera = read_header(io.StringIO(texto))
    assert cabecera.delimiter == separador
    assert cabecera.fields[0] == "codigo"


def test_las_lineas_en_blanco_antes_de_la_cabecera_se_saltan() -> None:
    archivo = io.StringIO("\n  \ncodigo,nombre\n1,a\n")
    cabecera = read_header(archivo)
    assert cabecera.fields == ("codigo", "nombre")
    assert cabecera.line == 3
    assert archivo.readline() == "1,a\n"  # el resto queda sin leer


def test_un_archivo_sin_nada_no_tiene_cabecera() -> None:
    with pytest.raises(CsvLoadError, match="vacio"):
        read_header(io.StringIO("\n\n"))


def test_detect_encoding_utf8_con_y_sin_tildes() -> None:
    assert detect_encoding(io.BytesIO(b"codigo\n1\n")) == "utf-8"
    assert detect_encoding(io.BytesIO("nombre\nPeña\n".encode())) == "utf-8"


def test_detect_encoding_reconoce_cp1252_y_deja_el_archivo_al_inicio() -> None:
    archivo = io.BytesIO("nombre\nPeña\nJosé\n".encode("cp1252"))
    assert detect_encoding(archivo) == "cp1252"
    assert archivo.tell() == 0


def test_detect_encoding_ve_una_secuencia_utf8_partida_entre_bloques() -> None:
    # "ñ" en UTF-8 son dos bytes; con bloques de 4 quedan en bloques distintos.
    contenido = b"abc" + "ñ".encode() + b"\n\xff\n"
    with pytest.raises(CsvLoadError) as error:
        detect_encoding(io.BytesIO(contenido), chunk_size=4)
    assert error.value.line == 2
