"""Pruebas de la carga masiva desde CSV contra el core compilado (issue #114)."""

from __future__ import annotations

import io
import tracemalloc

import pytest
from fastapi.testclient import TestClient

from engine.api.main import create_app
from engine.executor import QueryProcessor
from engine.executor.bulk_load import MAX_REPORTED_ERRORS, CsvLoadError
from engine.transactions import TransactionError

quipudb = pytest.importorskip(
    "quipudb_native",
    reason="los bindings no estan compilados: cmake -DQUIPUDB_BUILD_PYTHON=ON",
)

TABLA = (
    "CREATE TABLE alumnos (codigo INT PRIMARY KEY, nombre VARCHAR(16), "
    "promedio DOUBLE, activo BOOL, ingreso DATE) USING HEAP"
)


@pytest.fixture()
def processor(tmp_path):
    database = quipudb.Database(tmp_path / "catalogo.txt")
    processor = QueryProcessor(database, temp_dir=tmp_path / "tmp")
    processor.execute(TABLA)
    return processor


@pytest.fixture()
def cliente(tmp_path, processor):
    app = create_app(
        processor=processor,
        database=processor._database,
        native=quipudb,
    )
    with TestClient(app) as cliente:
        yield cliente


def _codigos(processor: QueryProcessor) -> list[int]:
    return sorted(fila[0] for fila in processor.execute("SELECT * FROM alumnos").rows)


def _subir(cliente: TestClient, contenido: str, tabla: str = "alumnos", **params):
    return cliente.post(
        f"/tables/{tabla}/load",
        files={"file": ("alumnos.csv", contenido.encode("utf-8"), "text/csv")},
        params=params,
    )


# ---------------------------------------------------------------------------
# Por HTTP
# ---------------------------------------------------------------------------


def test_carga_un_csv_con_las_columnas_en_otro_orden(cliente, processor) -> None:
    csv = (
        "nombre,ingreso,codigo,activo,promedio\n"
        "ana,2026-03-01,1,true,15.5\n"
        '"Perez, Luis",2025-08-15,2,false,12\n'
    )
    respuesta = _subir(cliente, csv)

    assert respuesta.status_code == 200
    assert respuesta.json() == {
        "table": "alumnos",
        "encoding": "utf-8",
        "inserted": 2,
        "failed": 0,
        "errors": [],
        "errors_truncated": False,
    }
    filas = processor.execute("SELECT * FROM alumnos WHERE codigo = 2").rows
    assert filas[0][:4] == (2, "Perez, Luis", 12.0, False)
    assert str(filas[0][4]) == "2025-08-15"


def test_las_filas_malas_se_reportan_con_su_linea_y_las_buenas_quedan(cliente, processor) -> None:
    csv = (
        "codigo,nombre,promedio,activo,ingreso\n"
        "1,ana,15,true,2026-01-01\n"  # linea 2
        "dos,beto,14,true,2026-01-01\n"  # linea 3: INT invalido
        "3,carla,14,quiza,2026-01-01\n"  # linea 4: BOOL invalido
        "\n"  # linea 5: en blanco, se salta
        "1,dora,13,true,2026-01-01\n"  # linea 6: clave repetida
        "7,eva,19,true,2026-02-30\n"  # linea 7: fecha que no existe
        "8,nombre demasiado largo,1,true,2026-01-01\n"  # linea 8: VARCHAR(16)
        "9,fito,11,false\n"  # linea 9: falta un campo
        "10,gaby,20,true,2026-01-01\n"  # linea 10
    )
    cuerpo = _subir(cliente, csv).json()

    assert cuerpo["inserted"] == 2
    assert cuerpo["failed"] == 6
    lineas = [error["line"] for error in cuerpo["errors"]]
    assert lineas == [3, 4, 6, 7, 8, 9]
    assert "columna codigo: 'dos' no es un INT" in cuerpo["errors"][0]["error"]
    assert "no se pudo insertar" in cuerpo["errors"][2]["error"]
    assert "tiene 4 campos" in cuerpo["errors"][5]["error"]
    assert _codigos(processor) == [1, 10]


def test_un_texto_con_saltos_de_linea_reporta_la_linea_donde_empieza(cliente) -> None:
    csv = (
        "codigo,nombre,promedio,activo,ingreso\n"
        '1,"dos\nlineas",15,true,2026-01-01\n'  # lineas 2 y 3
        "x,ana,15,true,2026-01-01\n"  # linea 4
    )
    cuerpo = _subir(cliente, csv).json()
    assert cuerpo["inserted"] == 1
    assert cuerpo["errors"][0]["line"] == 4


def test_un_csv_de_excel_en_espanol_se_carga_tal_cual(cliente, processor) -> None:
    # Excel con configuracion regional de Peru: punto y coma, coma decimal,
    # BOM, fin de linea CRLF, cabecera capitalizada y separador al final.
    contenido = (
        "\ufeffCodigo;Nombre;Promedio;Activo;Ingreso;\r\n"
        "1;Ana;15,5;TRUE;2026-03-01;\r\n"
        "2;Luis;12;FALSE;2026-03-02;\r\n"
    ).encode("utf-8")
    respuesta = cliente.post(
        "/tables/alumnos/load", files={"file": ("excel.csv", contenido, "text/csv")}
    )
    assert respuesta.json()["inserted"] == 2, respuesta.json()
    filas = processor.execute("SELECT * FROM alumnos WHERE codigo = 1").rows
    assert filas[0][:4] == (1, "Ana", 15.5, True)


def test_las_lineas_se_cuentan_desde_el_inicio_real_del_archivo(cliente) -> None:
    csv = "\n\ncodigo,nombre,promedio,activo,ingreso\nx,a,1,true,2026-01-01\n"
    cuerpo = _subir(cliente, csv).json()
    assert cuerpo["errors"][0]["line"] == 4


def test_una_peticion_sin_archivo_responde_con_la_forma_de_siempre(cliente) -> None:
    respuesta = cliente.post("/tables/alumnos/load")
    assert respuesta.status_code == 422
    assert respuesta.json()["error"] == "peticion invalida: file: Field required"

    respuesta = _subir(cliente, "codigo\n", atomic="quiza")
    assert respuesta.status_code == 422
    assert respuesta.json()["error"].startswith("peticion invalida: atomic:")


def test_una_cabecera_que_no_calza_no_inserta_nada(cliente, processor) -> None:
    csv = "codigo,nombre,nota,activo,ingreso\n1,ana,15,true,2026-01-01\n"
    respuesta = _subir(cliente, csv)

    assert respuesta.status_code == 400
    cuerpo = respuesta.json()
    assert "faltan 'promedio'" in cuerpo["error"]
    assert "sobran 'nota'" in cuerpo["error"]
    assert cuerpo["line"] == 1
    assert _codigos(processor) == []


def test_un_archivo_vacio_se_rechaza(cliente) -> None:
    respuesta = _subir(cliente, "")
    assert respuesta.status_code == 400
    assert "vacio" in respuesta.json()["error"]


def test_una_tabla_que_no_existe_es_404(cliente) -> None:
    respuesta = _subir(cliente, "a\n1\n", tabla="fantasma")
    assert respuesta.status_code == 404
    assert respuesta.json()["error"] == "la tabla 'fantasma' no existe"


def test_el_bom_de_excel_no_ensucia_la_primera_columna(cliente) -> None:
    csv = "﻿codigo,nombre,promedio,activo,ingreso\n1,ana,15,true,2026-01-01\n"
    assert _subir(cliente, csv).json()["inserted"] == 1


def test_un_csv_de_excel_en_cp1252_se_carga_con_sus_tildes(cliente, processor) -> None:
    # "Guardar como CSV" en un Excel de Windows: la n con tilde es 0xF1.
    contenido = "codigo;nombre;promedio;activo;ingreso\r\n1;Peña;15,5;true;2026-01-01\r\n"
    respuesta = cliente.post(
        "/tables/alumnos/load",
        files={"file": ("excel.csv", contenido.encode("cp1252"), "text/csv")},
    )
    assert respuesta.status_code == 200
    assert respuesta.json()["encoding"] == "cp1252"
    assert processor.execute("SELECT * FROM alumnos").rows[0][1] == "Peña"


def test_un_utf8_con_un_byte_roto_no_se_lee_como_cp1252(cliente, processor) -> None:
    # Tiene tildes bien escritas en UTF-8 y un byte suelto: leerlo como cp1252
    # cambiaria "Muñoz" por "MuÃ±oz" en silencio. Se rechaza con su linea.
    contenido = (
        "codigo,nombre,promedio,activo,ingreso\n1,Muñoz,15,true,2026-01-01\n".encode()
        + b"2,Pe\xf1a,1,true,2026-01-01\n"
    )
    respuesta = cliente.post(
        "/tables/alumnos/load", files={"file": ("roto.csv", contenido, "text/csv")}
    )
    assert respuesta.status_code == 400
    assert respuesta.json()["line"] == 3
    assert "UTF-8" in respuesta.json()["error"]
    assert _codigos(processor) == []


def test_un_byte_que_no_es_texto_en_ninguna_codificacion_da_su_linea(cliente, processor) -> None:
    # 0x81 no es UTF-8 valido ni esta definido en cp1252. Mas grande que un
    # bloque de lectura, para que el error caiga en otro.
    filas = "".join(f"{i},n{i},10,true,2026-01-01\n" for i in range(1, 5001))
    contenido = ("codigo,nombre,promedio,activo,ingreso\n" + filas).encode() + (
        b"5001,\x81,1,true,2026-01-01\n"
    )
    respuesta = cliente.post(
        "/tables/alumnos/load", files={"file": ("x.csv", contenido, "text/csv")}
    )
    assert respuesta.status_code == 400
    assert respuesta.json()["line"] == 5002
    assert _codigos(processor) == []


def test_una_carga_atomica_que_falla_no_deja_ninguna_fila(cliente, processor) -> None:
    filas = "".join(f"{i},n{i},10,true,2026-01-01\n" for i in range(1, 300))
    csv = "codigo,nombre,promedio,activo,ingreso\n" + filas + "300,n300,diez,true,2026-01-01\n"
    respuesta = _subir(cliente, csv, atomic="true")

    assert respuesta.status_code == 400
    cuerpo = respuesta.json()
    assert cuerpo["line"] == 301
    assert cuerpo["error"].startswith("linea 301: columna promedio: 'diez' no es un DOUBLE")
    assert "no se inserto ninguna fila" in cuerpo["error"]
    assert _codigos(processor) == []


def test_una_carga_atomica_sin_errores_deja_todo(cliente, processor) -> None:
    csv = "codigo,nombre,promedio,activo,ingreso\n1,a,1,true,2026-01-01\n2,b,2,false,2026-01-02\n"
    respuesta = _subir(cliente, csv, atomic="true")
    assert respuesta.json()["inserted"] == 2
    assert _codigos(processor) == [1, 2]
    # El lock de la carga se libero: se puede seguir usando la tabla.
    processor.execute("INSERT INTO alumnos VALUES (3, 'c', 3.0, TRUE, DATE '2026-01-03')")


def test_no_se_carga_dentro_de_una_transaccion(cliente, processor) -> None:
    cliente.post("/query", json={"sql": "BEGIN TRANSACTION"})
    respuesta = _subir(cliente, "codigo,nombre,promedio,activo,ingreso\n")
    assert respuesta.status_code == 400
    assert "dentro de una transaccion" in respuesta.json()["error"]
    cliente.post("/query", json={"sql": "END TRANSACTION"})


def test_un_csv_de_pura_basura_devuelve_solo_las_primeras_fallas(cliente) -> None:
    filas = "x,a,1,true,2026-01-01\n" * (MAX_REPORTED_ERRORS + 50)
    cuerpo = _subir(cliente, "codigo,nombre,promedio,activo,ingreso\n" + filas).json()
    assert cuerpo["failed"] == MAX_REPORTED_ERRORS + 50
    assert len(cuerpo["errors"]) == MAX_REPORTED_ERRORS
    assert cuerpo["errors_truncated"] is True


def test_la_carga_mantiene_los_indices_secundarios(cliente, processor) -> None:
    processor.execute("CREATE INDEX por_promedio ON alumnos (promedio) USING BPLUS")
    filas = "".join(f"{i},n{i},{i % 5},true,2026-01-01\n" for i in range(1, 51))
    _subir(cliente, "codigo,nombre,promedio,activo,ingreso\n" + filas)

    resultado = processor.execute("SELECT * FROM alumnos WHERE promedio = 3")
    assert len(resultado.rows) == 10
    indice = processor._database.index("alumnos", "por_promedio")
    assert len(indice.search(3.0)) == 10


# ---------------------------------------------------------------------------
# 100 000 filas sin tenerlas en memoria
# ---------------------------------------------------------------------------


def _csv_grande(ruta, n: int) -> None:
    with open(ruta, "w", encoding="utf-8", newline="") as archivo:
        archivo.write("promedio,codigo,nombre,activo,ingreso\n")
        archivo.writelines(
            f"{i % 20}.5,{i},alumno{i},{'true' if i % 2 else 'false'},2026-01-01\n"
            for i in range(n)
        )


def test_cien_mil_filas_se_cargan_sin_acumularlas_en_memoria(tmp_path, processor) -> None:
    ruta = tmp_path / "grande.csv"
    _csv_grande(ruta, 100_000)
    tamano = ruta.stat().st_size

    tracemalloc.start()
    try:
        with open(ruta, encoding="utf-8", newline="") as archivo:
            reporte = processor.load_csv("alumnos", archivo)
        _, pico = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert reporte.inserted == 100_000
    assert reporte.failed == 0
    # Solo la lista de filas ya ocuparia varias veces el archivo. Leyendo por
    # partes, el pico de Python no llega ni a una decima parte de el.
    assert pico < tamano / 10, f"pico de {pico} bytes para un archivo de {tamano}"
    filas = processor.execute("SELECT * FROM alumnos WHERE codigo = 99999").rows
    assert filas[0][2] == 19.5


def test_por_http_tambien_entran_cien_mil_filas(tmp_path, cliente, processor) -> None:
    ruta = tmp_path / "grande.csv"
    _csv_grande(ruta, 100_000)
    with open(ruta, "rb") as archivo:
        respuesta = cliente.post(
            "/tables/alumnos/load", files={"file": ("grande.csv", archivo, "text/csv")}
        )
    assert respuesta.json()["inserted"] == 100_000
    assert cliente.get("/tables").json()[0]["record_count"] == 100_000


# ---------------------------------------------------------------------------
# Directo sobre el procesador
# ---------------------------------------------------------------------------


def test_load_csv_de_una_tabla_inexistente(processor) -> None:
    with pytest.raises(CsvLoadError, match="no existe"):
        processor.load_csv("fantasma", io.StringIO("a\n"))


def test_load_csv_dentro_de_una_transaccion(processor) -> None:
    processor.execute("BEGIN TRANSACTION")
    with pytest.raises(TransactionError):
        processor.load_csv("alumnos", io.StringIO("codigo\n"))
    processor.execute("END TRANSACTION")


def test_si_el_texto_deja_de_ser_utf8_a_mitad_la_carga_parcial_se_detiene(processor) -> None:
    # Sin pasar por la API no hay chequeo previo: el procesador se defiende
    # solo. El prefijo es mas grande que el bloque del decodificador, asi que
    # la cabecera y las primeras filas se leen antes del error.
    filas = "".join(f"{i},n{i},10,true,2026-01-01\n" for i in range(1, 2001))
    crudo = ("codigo,nombre,promedio,activo,ingreso\n" + filas).encode() + b"\xff\n"
    texto = io.TextIOWrapper(io.BytesIO(crudo), encoding="utf-8", newline="")

    reporte = processor.load_csv("alumnos", texto)

    assert reporte.failed == 1
    assert "UTF-8" in reporte.errors[0].error
    assert 0 < reporte.inserted <= 2000


def test_una_falla_atomica_libera_el_lock(processor) -> None:
    csv = "codigo,nombre,promedio,activo,ingreso\n1,a,x,true,2026-01-01\n"
    with pytest.raises(CsvLoadError):
        processor.load_csv("alumnos", io.StringIO(csv), atomic=True)
    # Si el lock siguiera tomado, este INSERT esperaria hasta el timeout.
    processor.execute("INSERT INTO alumnos VALUES (1, 'a', 1.0, TRUE, DATE '2026-01-01')")
    assert _codigos(processor) == [1]
