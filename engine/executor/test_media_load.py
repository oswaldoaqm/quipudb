"""Carga de vectores ya extraidos sin bindings, con el mismo DML que INSERT."""

from __future__ import annotations

import math

import pytest

from engine.executor import QueryProcessor
from engine.executor.bulk_load import MAX_REPORTED_ERRORS
from engine.executor.media_load import MediaColumns, MediaLoadError, MediaLoadReport, MediaRow
from engine.executor.test_processor import _FakeDatabase, _InvalidRecord, _NativeModule
from engine.transactions import TransactionError

TABLE = (
    "CREATE TABLE objetos (id INT PRIMARY KEY, nombre VARCHAR(32), "
    "ruta VARCHAR(256), rasgos VECTOR(2)) USING HEAP"
)


@pytest.fixture()
def processor():
    database = _FakeDatabase()
    processor = QueryProcessor(database, native_module=_NativeModule)
    processor.execute(TABLE)
    return processor


def row(identifier=1, name="foto.png", vector=(0.6, 0.8)):
    return MediaRow(identifier, name, "/dataset/" + name, vector)


def test_carga_registros_y_el_vector_sin_generar_sql(processor):
    report = processor.load_media_batch("objetos", [row(name="foto'de_ana.png")], dimension=2)
    assert report.inserted == 1
    assert report.failed == 0
    assert processor._database.tables["objetos"].records == [
        [1, "foto'de_ana.png", "/dataset/foto'de_ana.png", (0.6, 0.8)]
    ]


def test_una_clave_repetida_no_detiene_las_otras_filas(processor):
    report = processor.load_media_batch("objetos", [row(1), row(1), row(2)])
    assert (report.inserted, report.failed) == (2, 1)
    assert "clave primaria" in report.errors[0].error
    assert [r[0] for r in processor._database.tables["objetos"].records] == [1, 2]


@pytest.mark.parametrize("vector", [(1.0,), (math.nan, 0.0), (math.inf, 0.0), (1e40, 0.0)])
def test_vectores_invalidos_se_reportan_sin_escribir(processor, vector):
    report = processor.load_media_batch("objetos", [row(vector=vector)])
    assert report.failed == 1
    assert "VECTOR(2)" in report.errors[0].error
    assert not processor._database.tables["objetos"].records


def test_el_vector_nulo_es_una_fila_valida(processor):
    assert processor.load_media_batch("objetos", [row(vector=(0.0, 0.0))]).inserted == 1


def test_comprueba_bytes_utf8_y_no_caracteres(processor):
    report = processor.load_media_batch("objetos", [row(name="ñ" * 17)])
    assert report.failed == 1
    assert "VARCHAR(32)" in report.errors[0].error


@pytest.mark.parametrize("identifier", [True, 1.5, 2**31, -(2**31) - 1])
def test_no_trunca_identificadores_invalidos(processor, identifier):
    report = processor.load_media_batch("objetos", [row(identifier)])
    assert report.failed == 1


def test_dimension_del_vocabulario_incorrecta_se_rechaza_antes_de_insertar(processor):
    with pytest.raises(MediaLoadError, match="VECTOR\\(2\\)"):
        processor.load_media_batch("objetos", [row()], dimension=3)
    assert not processor._database.tables["objetos"].records


def test_tabla_inexistente_y_columnas_incorrectas_no_escriben(processor):
    with pytest.raises(MediaLoadError, match="no existe"):
        processor.load_media_batch("ausente", [row()])
    with pytest.raises(MediaLoadError, match="cuatro columnas"):
        processor.load_media_batch("objetos", [row()], columns=MediaColumns(vector="otro"))


def test_no_interfiere_con_una_transaccion_abierta(processor):
    processor.execute("BEGIN TRANSACTION")
    with pytest.raises(TransactionError, match="terminar"):
        processor.load_media_batch("objetos", [row()])
    processor.execute("END TRANSACTION")
    assert processor.load_media_batch("objetos", [row()]).inserted == 1


def test_respeta_el_orden_del_esquema_y_nombres_personalizados(processor):
    processor.execute(
        "CREATE TABLE fotos (features VECTOR(2), archivo VARCHAR(32), "
        "codigo INT PRIMARY KEY, ubicacion VARCHAR(256))"
    )
    columns = MediaColumns(id="codigo", name="archivo", path="ubicacion", vector="features")
    report = processor.load_media_batch("fotos", [row()], columns=columns)
    assert report.inserted == 1
    assert processor._database.tables["fotos"].records[0] == [
        (0.6, 0.8), "foto.png", 1, "/dataset/foto.png"
    ]


def test_mantiene_indices_y_revierte_la_fila_si_el_indice_falla(processor, monkeypatch):
    database = processor._database
    index = database.add_index("objetos", "por_nombre", 1)
    insert = index.insert

    def falla_despues_de_insertar(key, rid):
        insert(key, rid)
        if key == "roto.png":
            raise _InvalidRecord("fallo al escribir el indice")

    monkeypatch.setattr(index, "insert", falla_despues_de_insertar)
    report = processor.load_media_batch("objetos", [row(1, "roto.png"), row(2, "bien.png")])
    assert (report.inserted, report.failed) == (1, 1)
    assert len(database.tables["objetos"].records) == 1
    assert [entry[0] for entry in index.entries] == ["bien.png"]


def test_reporte_acotado_aun_con_muchos_fallos(processor):
    report = processor.load_media_batch("objetos", (row(i, "a" * 33) for i in range(1000)))
    assert report.failed == 1000
    assert len(report.errors) == MAX_REPORTED_ERRORS
    assert report.errors_truncated
    accumulated = MediaLoadReport("objetos")
    accumulated.merge(report)
    accumulated.merge(report)
    assert accumulated.failed == 2000
    assert len(accumulated.errors) == MAX_REPORTED_ERRORS
