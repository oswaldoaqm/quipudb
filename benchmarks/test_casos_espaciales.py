"""Pruebas de la suite espacial #131: seleccion de consultas, casos y una corrida real."""

from __future__ import annotations

import csv
import math

import pytest

from benchmarks.scripts import ejecutar_benchmarks as cli
from benchmarks.scripts.banco_pruebas import ejecutar_banco
from benchmarks.scripts.cargar_puntos import leer_puntos
from benchmarks.scripts.casos_espaciales import (
    METROS_POR_GRADO,
    RADIOS_CRUCE_KM,
    RADIOS_KM,
    VALORES_K,
    caso_knn,
    caso_radio,
    casos_espaciales,
    distancia,
    radio_en_unidades,
    seleccionar_centros,
)
from benchmarks.scripts.generar_puntos import generar_dataset


@pytest.fixture(scope="module")
def datasets(tmp_path_factory):
    carpeta = tmp_path_factory.mktemp("puntos")
    return {n: leer_puntos(generar_dataset(n, carpeta), n) for n in (1_000, 10_000)}


class NativoFalso:
    """Solo para construir la lista de casos: ninguno se ejecuta."""


def test_los_centros_son_los_mismos_en_todos_los_tamanos(datasets):
    """Los datasets estan anidados: los centros salen de los primeros 1 000
    puntos, asi que entre tamanos solo cambia la densidad alrededor."""
    chico = seleccionar_centros(datasets[1_000])
    grande = seleccionar_centros(datasets[10_000])
    assert chico == grande
    assert len(chico) == 100
    assert len(set(chico)) == 100
    coordenadas = {fila[2:] for fila in datasets[1_000].registros}
    assert set(chico) <= coordenadas


@pytest.mark.parametrize("cantidad", [0, 1_001])
def test_rechaza_cantidades_de_consultas_fuera_de_rango(datasets, cantidad):
    with pytest.raises(ValueError, match="consultas"):
        seleccionar_centros(datasets[1_000], cantidad)


def test_radio_en_unidades_de_cada_metrica():
    assert radio_en_unidades(5, "haversine") == 5_000.0
    assert radio_en_unidades(5, "euclidiana") == pytest.approx(5_000.0 / METROS_POR_GRADO)


def test_oraculo_de_distancias():
    lima, arequipa = (-12.0464, -77.0428), (-16.3989, -71.5375)
    # Lima-Arequipa por circulo maximo: unos 765 km.
    assert distancia(lima, arequipa, "haversine") == pytest.approx(765_000, rel=0.01)
    assert distancia(lima, arequipa, "euclidiana") == pytest.approx(
        math.hypot(4.3525, 5.5053), rel=1e-6
    )


def test_la_suite_cubre_el_enunciado_y_el_cruce():
    casos = casos_espaciales(NativoFalso())
    nombres = {caso.nombre for caso in casos}
    assert len(nombres) == len(casos)
    assert "rtree_construccion_indice" in nombres
    # El secuencial no tiene construccion: no hay fila ficticia con tiempo cero.
    assert "secuencial_construccion_indice" not in nombres
    for metrica in ("haversine", "euclidiana"):
        for tecnica in ("secuencial", "rtree"):
            for r in RADIOS_KM:
                assert f"{tecnica}_radio_{r}km_{metrica}" in nombres
            for k in VALORES_K:
                assert f"{tecnica}_knn_{k}_{metrica}" in nombres
    for r in RADIOS_CRUCE_KM:
        assert f"rtree_radio_{r}km_haversine" in nombres
        assert f"rtree_radio_{r}km_euclidiana" not in nombres
    assert len(casos_espaciales(NativoFalso(), cruce=False)) == 1 + 2 * 2 * 6


def test_rechaza_metricas_desconocidas():
    with pytest.raises(ValueError, match="metrica"):
        caso_radio(NativoFalso(), "rtree", 1, "manhattan")
    with pytest.raises(ValueError, match="metrica"):
        caso_knn(NativoFalso(), "rtree", 10, "manhattan")


def test_cli_valida_la_cantidad_de_consultas(monkeypatch, capsys):
    monkeypatch.setattr(
        "sys.argv", ["ejecutar_benchmarks.py", "--suite", "espacial", "--consultas", "1001"]
    )
    with pytest.raises(SystemExit, match="2"):
        cli.main()
    assert "en la suite espacial consultas debe estar entre 1 y 1000" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Corrida real pequena
# ---------------------------------------------------------------------------


@pytest.fixture
def nativo_real():
    return pytest.importorskip(
        "quipudb_native",
        reason="requiere bindings compilados para este Python y configurados en PYTHONPATH",
    )


def test_integracion_espacial_real(nativo_real, datasets, tmp_path):
    """Cada caso valida su respuesta contra la busqueda secuencial: si el R-Tree
    devolviera otra cosa, el banco abortaria antes de exportar."""
    casos = [
        caso
        for caso in casos_espaciales(nativo_real, consultas=10)
        if caso.operacion in ("construccion_indice", "radio_5km", "knn_50", "radio_2000km")
    ]
    rutas = ejecutar_banco(
        casos,
        [datasets[1_000]],
        salida=tmp_path / "res",
        temporales=tmp_path,
        calentamientos=0,
        repeticiones=1,
    )
    with rutas["resumen"].open(encoding="utf-8") as archivo:
        resumen = {fila["caso"]: fila for fila in csv.DictReader(archivo)}
    assert set(resumen) == {caso.nombre for caso in casos}

    paginas = {caso: float(fila["mediana_paginas_leidas"]) for caso, fila in resumen.items()}
    # El secuencial lee el Heap entero en cada consulta, sin importar el radio.
    assert paginas["secuencial_radio_5km_haversine"] == paginas["secuencial_radio_2000km_haversine"]
    # Con un radio chico el R-Tree poda; con uno que cubre el Peru, ya no.
    assert paginas["rtree_radio_5km_haversine"] < paginas["secuencial_radio_5km_haversine"]
    assert paginas["rtree_radio_5km_haversine"] < paginas["rtree_radio_2000km_haversine"]
    assert int(resumen["rtree_knn_50_euclidiana"]["mediana_indices_bytes"]) > 0
    assert int(resumen["secuencial_knn_50_euclidiana"]["mediana_indices_bytes"]) == 0

    with rutas["entorno"].open(encoding="utf-8") as archivo:
        entorno = {fila["clave"]: fila["valor"] for fila in csv.DictReader(archivo)}
    # Las dos tecnicas devolvieron exactamente los mismos puntos.
    assert (
        entorno["caso.rtree_radio_5km_haversine.1000.resultados_totales"]
        == entorno["caso.secuencial_radio_5km_haversine.1000.resultados_totales"]
    )
    assert entorno["caso.rtree_radio_2000km_haversine.1000.resultados_totales"] == "10000"
