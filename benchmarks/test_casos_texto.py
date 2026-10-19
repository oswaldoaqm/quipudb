"""Pruebas de la suite de texto #172: casos, CLI y una corrida real pequena."""

from __future__ import annotations

import csv

import pytest

from benchmarks.scripts import ejecutar_benchmarks as cli
from benchmarks.scripts.banco_pruebas import ejecutar_banco
from benchmarks.scripts.cargar_documentos import ConsultaTexto, leer_consultas, leer_documentos
from benchmarks.scripts.casos_texto import (
    CONSULTAS_POR_LONGITUD,
    caso_consulta,
    casos_texto,
    consultas_de_longitud,
    nombre_longitud,
)
from benchmarks.scripts.generar_documentos import generar, leer_quora
from benchmarks.test_generar_documentos import _zip_sintetico

CONSULTAS = tuple(
    ConsultaTexto(i, longitud, f"consulta {i}")
    for i, longitud in enumerate(("1", "3", "5+") * 4, start=1)
)


class NativoFalso:
    """Solo para construir la lista de casos: ninguno se ejecuta."""


def test_nombres_de_longitud_validos_en_un_caso():
    assert [nombre_longitud(n) for n in ("1", "3", "5+")] == ["1", "3", "5omas"]


def test_toma_las_primeras_consultas_de_cada_longitud():
    assert [c.id for c in consultas_de_longitud(CONSULTAS, "3", 2)] == [2, 5]
    assert len(consultas_de_longitud(CONSULTAS, "5+", 4)) == 4
    with pytest.raises(ValueError, match="se pidieron 5"):
        consultas_de_longitud(CONSULTAS, "1", 5)
    with pytest.raises(ValueError, match="se pidieron 0"):
        consultas_de_longitud(CONSULTAS, "1", 0)
    with pytest.raises(ValueError, match="longitud desconocida"):
        consultas_de_longitud(CONSULTAS, "2", 1)


def test_una_construccion_comun_y_consultas_por_ranking_y_longitud():
    casos = casos_texto(NativoFalso(), CONSULTAS, 4)
    # La construccion es la misma para las dos tecnicas: un solo caso, no dos.
    assert [(c.tecnica, c.operacion) for c in casos] == [
        ("spimi", "construccion_indice"),
        ("tf_idf", "consulta_1_palabras"),
        ("tf_idf", "consulta_3_palabras"),
        ("tf_idf", "consulta_5omas_palabras"),
        ("bm25", "consulta_1_palabras"),
        ("bm25", "consulta_3_palabras"),
        ("bm25", "consulta_5omas_palabras"),
    ]
    assert len({c.nombre for c in casos}) == len(casos)


def test_rechaza_rankings_desconocidos():
    with pytest.raises(ValueError, match="ranking desconocido"):
        caso_consulta(NativoFalso(), "pagerank", "1", CONSULTAS, 1)


@pytest.mark.parametrize(
    "argumentos, mensaje",
    [
        (["--consultas", "101"], f"consultas debe estar entre 1 y {CONSULTAS_POR_LONGITUD}"),
        (["--page-size", "8192"], "la suite texto usa el page-size del binding"),
    ],
)
def test_cli_valida_la_suite_texto(monkeypatch, capsys, argumentos, mensaje):
    monkeypatch.setattr("sys.argv", ["ejecutar_benchmarks.py", "--suite", "texto", *argumentos])
    with pytest.raises(SystemExit, match="2"):
        cli.main()
    assert mensaje in capsys.readouterr().err


def test_cli_sin_dataset_dice_como_generarlo(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(
        "sys.argv",
        ["ejecutar_benchmarks.py", "--suite", "texto", "--datasets", str(tmp_path)],
    )
    with pytest.raises(SystemExit, match="1"):
        cli.main()
    assert "generar_documentos.py" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Corrida real pequena
# ---------------------------------------------------------------------------


@pytest.fixture
def nativo_real():
    return pytest.importorskip(
        "quipudb_native",
        reason="requiere bindings compilados para este Python y configurados en PYTHONPATH",
    )


@pytest.fixture(scope="module")
def generado(tmp_path_factory):
    raiz = tmp_path_factory.mktemp("texto")
    quora = leer_quora(_zip_sintetico(raiz / "quora.zip"))
    generar(quora, raiz / "datasets", (1_000,), preguntas=20)
    return raiz / "datasets"


def test_integracion_texto_real(nativo_real, generado, tmp_path):
    """Dos repeticiones: cada consulta tiene que responder lo mismo en las dos, y
    lo que cuenta SPIMI tambien."""
    consultas = leer_consultas(generado / "consultas.csv")
    casos = casos_texto(nativo_real, consultas, 10)
    rutas = ejecutar_banco(
        casos,
        [leer_documentos(generado / "documentos_1000.csv", 1_000)],
        salida=tmp_path / "res",
        temporales=tmp_path,
        calentamientos=0,
        repeticiones=2,
    )
    with rutas["resumen"].open(encoding="utf-8") as archivo:
        resumen = {fila["caso"]: fila for fila in csv.DictReader(archivo)}
    assert set(resumen) == {caso.nombre for caso in casos}
    with rutas["entorno"].open(encoding="utf-8") as archivo:
        entorno = {fila["clave"]: fila["valor"] for fila in csv.DictReader(archivo)}

    construccion = resumen["spimi_construccion_indice"]
    assert float(construccion["mediana_paginas_escritas"]) > 0
    assert float(construccion["mediana_indices_bytes"]) > 0
    prefijo = "caso.spimi_construccion_indice.1000."
    assert int(entorno[prefijo + "bloques_spimi"]) >= 1
    assert (
        0
        < int(entorno[prefijo + "memoria_pico_spimi_bytes"])
        <= int(entorno[prefijo + "memoria_presupuesto_spimi_bytes"])
    )
    assert entorno[prefijo + "memoria_rids_bytes"] == str(8 * 1_000)

    for longitud in ("1", "3", "5omas"):
        tfidf = resumen[f"tf_idf_consulta_{longitud}_palabras"]
        bm25 = resumen[f"bm25_consulta_{longitud}_palabras"]
        # Consultar no escribe, y los dos rankings recorren las mismas listas de
        # postings y las mismas estadisticas por documento: leen las mismas paginas.
        assert float(tfidf["mediana_paginas_escritas"]) == 0
        assert float(tfidf["mediana_paginas_leidas"]) > 0
        assert tfidf["mediana_paginas_leidas"] == bm25["mediana_paginas_leidas"]
        # El indice es uno solo: el mismo espacio para las dos tecnicas.
        assert tfidf["mediana_indices_bytes"] == construccion["mediana_indices_bytes"]
        assert bm25["mediana_indices_bytes"] == construccion["mediana_indices_bytes"]
        clave = f"caso.bm25_consulta_{longitud}_palabras.1000."
        assert int(entorno[clave + "consultas"]) == 10
        assert float(entorno[clave + "coincidentes_por_consulta"]) >= 1
