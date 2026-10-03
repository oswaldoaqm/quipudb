"""Pruebas de #132: consultas de PostGIS y generador de graficas, sin servidor ni CSV oficiales."""

from __future__ import annotations

import csv
import hashlib
import json

import pytest

from benchmarks.scripts import bench_postgis as pg
from benchmarks.scripts import generar_graficas_espacial as gr
from benchmarks.scripts.banco_pruebas import (
    CABECERA_ENTORNO,
    CABECERA_RESUMEN,
    escribir_csv,
)
from benchmarks.scripts.casos_espaciales import RADIOS_CRUCE_KM, casos_espaciales

# ---------------------------------------------------------------------------
# Consultas de PostGIS
# ---------------------------------------------------------------------------


def test_cada_metrica_usa_su_tipo_y_su_tabla():
    assert pg.tabla_de("haversine") == "puntos_geog"
    assert pg.tabla_de("euclidiana") == "puntos_geom"
    assert pg.indice_de("haversine") == "puntos_geog_gist"


def test_el_radio_haversine_usa_la_esfera_y_no_el_esferoide():
    """La Haversine del core es esferica: geography con use_spheroid = false
    calcula lo mismo; con el esferoide las cantidades no coincidirian."""
    sql = pg.sql_radio((-12.0464, -77.0428), 5_000.0, "haversine")
    assert "::geography" in sql
    assert sql.endswith(", 5000.0, false)")
    # Longitud primero, como en ST_MakePoint(x, y).
    assert "ST_MakePoint(-77.0428, -12.0464)" in sql


def test_la_euclidiana_usa_geometry_en_grados():
    sql = pg.sql_radio((-12.0, -77.0), 0.05, "euclidiana")
    assert "::geography" not in sql
    assert sql.endswith(", 0.05)")


def test_el_knn_ordena_con_el_operador_de_distancia_del_indice():
    sql = pg.sql_knn((-12.0, -77.0), 50, "haversine")
    assert "ORDER BY ubicacion <-> " in sql
    assert sql.endswith("LIMIT 50")


def test_los_literales_no_admiten_texto_arbitrario():
    with pytest.raises(ValueError):
        pg.punto_sql("1); DROP TABLE x; --", 0.0, "haversine")


def test_resumen_de_plan_recorre_todos_los_nodos():
    plan = {
        "Node Type": "Bitmap Heap Scan",
        "Plans": [{"Node Type": "Bitmap Index Scan", "Index Name": "puntos_geog_gist"}],
    }
    assert pg.resumen_plan(plan) == "Bitmap Heap Scan > Bitmap Index Scan(puntos_geog_gist)"


def test_postgis_mide_los_mismos_casos_que_la_suite_espacial():
    """Sin la construccion, PostGIS y QuipuDB tienen que cubrir exactamente las
    mismas (operacion, metrica): si no, las graficas compararian cosas distintas."""

    class Nativo:
        pass

    quipudb = {
        (c.operacion, c.nombre.rsplit("_", 1)[1])
        for c in casos_espaciales(Nativo())
        if c.tecnica == "rtree" and c.operacion != "construccion_indice"
    }
    postgis = {(op, metrica) for op, metrica, _ in pg.operaciones(cruce=True)}
    assert postgis == quipudb
    sin_cruce = {op for op, _, _ in pg.operaciones(cruce=False)}
    assert not sin_cruce & {f"radio_{r}km" for r in RADIOS_CRUCE_KM}


def test_referencias_de_quipudb_se_leen_del_entorno(tmp_path):
    ruta = tmp_path / "entorno.csv"
    escribir_csv(
        ruta,
        CABECERA_ENTORNO,
        [
            {
                "ejecucion": "x",
                "clave": "caso.rtree_radio_5km_haversine.1000.resultados_totales",
                "valor": "1915",
            },
            {
                "ejecucion": "x",
                "clave": "caso.secuencial_radio_5km_haversine.1000.resultados_totales",
                "valor": "1915",
            },
            {
                "ejecucion": "x",
                "clave": "caso.rtree_radio_5km_haversine.1000.metrica",
                "valor": "haversine",
            },
        ],
    )
    assert pg.referencias_quipudb(ruta) == {("radio_5km_haversine", 1000): 1915}
    assert pg.referencias_quipudb(None) == {}


# ---------------------------------------------------------------------------
# Generador de graficas y tablas
# ---------------------------------------------------------------------------

TECNICAS = ("secuencial", "rtree", "postgis_gist")


def _fila(caso, n, tiempo_ns, paginas=10, datos=4096, indices=8192, operaciones=100):
    tecnica = next(t for t in ("postgis_gist", "secuencial", "rtree") if caso.startswith(t))
    fila = dict.fromkeys(CABECERA_RESUMEN, 0)
    fila.update(
        ejecucion="sintetica",
        caso=caso,
        tecnica=tecnica,
        operacion="x",
        n_registros=n,
        n_operaciones=operaciones,
        repeticiones=5,
        mediana_tiempo_ns=tiempo_ns,
        mediana_paginas_leidas=paginas,
        mediana_datos_bytes=datos,
        mediana_indices_bytes=indices,
        min_tiempo_ns=tiempo_ns * 0.9,
        max_tiempo_ns=tiempo_ns * 1.1,
    )
    return fila


@pytest.fixture
def fuentes(tmp_path):
    """Dos corridas sinteticas con todos los casos que el generador necesita."""
    entrada = tmp_path / "results"
    entrada.mkdir()
    resumenes = {"131": [], "132": []}
    entornos = {"131": [], "132": []}
    operaciones = [f"radio_{r}km" for r in gr.RADIOS_CRUCE] + [f"knn_{k}" for k in gr.VALORES_K]
    for n in gr.TAMANOS:
        resumenes["131"].append(
            _fila("rtree_construccion_indice", n, 2e6 * n / 1000, operaciones=1)
        )
        for metrica in gr.METRICAS:
            resumenes["132"].append(
                _fila(
                    f"postgis_gist_construccion_indice_{metrica}", n, 5e5 * n / 1000, operaciones=1
                )
            )
        for op in operaciones:
            for metrica in gr.METRICAS:
                for i, tecnica in enumerate(TECNICAS):
                    corrida = "132" if tecnica == "postgis_gist" else "131"
                    resumenes[corrida].append(_fila(f"{tecnica}_{op}_{metrica}", n, (i + 1) * 1e6))
                entornos["131"].append(
                    {
                        "ejecucion": "s",
                        "clave": f"caso.rtree_{op}_{metrica}.{n}.resultados_totales",
                        "valor": "500",
                    }
                )
                entornos["132"].append(
                    {
                        "ejecucion": "s",
                        "clave": f"caso.postgis_gist_{op}_{metrica}.{n}.plan_natural",
                        "valor": "Seq Scan",
                    }
                )
    manifiesto = {"corridas": {}}
    for corrida in ("131", "132"):
        archivos = {}
        for tipo, cabecera, filas in (
            ("resumen", CABECERA_RESUMEN, resumenes[corrida]),
            ("entorno", CABECERA_ENTORNO, entornos[corrida]),
            ("mediciones", ("x",), []),
        ):
            ruta = entrada / f"c{corrida}_{tipo}.csv"
            escribir_csv(ruta, cabecera, filas)
            archivos[tipo] = {
                "nombre": ruta.name,
                "sha256": hashlib.sha256(ruta.read_bytes()).hexdigest(),
            }
        manifiesto["corridas"][corrida] = {"archivos": archivos}
    ruta_manifiesto = tmp_path / "fuentes.json"
    ruta_manifiesto.write_text(json.dumps(manifiesto), encoding="utf-8")
    informe = tmp_path / "informe.md"
    informe.write_text(f"antes\n{gr.INICIO}\nviejo\n{gr.FIN}\ndespues\n", encoding="utf-8")
    return entrada, ruta_manifiesto, informe


def test_las_tablas_salen_de_las_fuentes(fuentes):
    entrada, manifiesto, _ = fuentes
    resumen, entorno = gr.cargar(entrada, manifiesto)
    bloque = gr.tablas(resumen, entorno)
    # 1 ms por consulta el secuencial, 2 el R-Tree, 3 GiST: el cociente es 0,667.
    assert "| radio_1km | haversine | 0,010 | 0,020 | 0,030 | 0,667 |" in bloque
    assert "| 2 000 km | 5 | " in bloque
    assert "Seq Scan" in bloque
    # Paginas: 10 por lote de 100 -> 0,1 por consulta; 4 KiB en QuipuDB y 8 en PostgreSQL.
    assert bloque.count("| 0 | 0 | 1 |") >= 1


def test_un_hash_distinto_aborta_antes_de_escribir(fuentes, tmp_path):
    entrada, manifiesto, informe = fuentes
    with (entrada / "c131_resumen.csv").open("a", encoding="utf-8") as archivo:
        archivo.write("\n")
    with pytest.raises(ValueError, match="SHA-256"):
        gr.generar(entrada, tmp_path / "graficas", manifiesto, informe)
    assert "viejo" in informe.read_text(encoding="utf-8")
    assert not (tmp_path / "graficas").exists()


def test_genera_las_figuras_y_conserva_el_texto_fuera_de_los_marcadores(fuentes, tmp_path):
    pytest.importorskip("matplotlib", reason="renderizado requiere Matplotlib")
    entrada, manifiesto, informe = fuentes
    escritas = gr.generar(entrada, tmp_path / "graficas", manifiesto, informe)
    assert len(escritas) == 2 * len(gr.FIGURAS)
    assert all(ruta.stat().st_size > 0 for ruta in escritas)
    texto = informe.read_text(encoding="utf-8")
    assert texto.startswith("antes\n") and texto.endswith("despues\n")
    assert "viejo" not in texto


def test_el_manifiesto_oficial_apunta_a_las_dos_corridas():
    fuentes = json.loads(gr.MANIFIESTO.read_text(encoding="utf-8"))
    assert set(fuentes["corridas"]) == {"131", "132"}
    for corrida in fuentes["corridas"].values():
        assert set(corrida["archivos"]) == {"mediciones", "resumen", "entorno"}
        for archivo in corrida["archivos"].values():
            assert archivo["nombre"].startswith(corrida["ejecucion"])
            assert len(archivo["sha256"]) == 64


def test_las_fuentes_oficiales_coinciden_si_estan_presentes():
    fuentes = json.loads(gr.MANIFIESTO.read_text(encoding="utf-8"))
    rutas = [
        (gr.ENTRADA / a["nombre"], a["sha256"])
        for c in fuentes["corridas"].values()
        for a in c["archivos"].values()
    ]
    if not all(ruta.is_file() for ruta, _ in rutas):
        pytest.skip("los CSV oficiales estan fuera de Git; copialos a benchmarks/results")
    for ruta, esperado in rutas:
        assert hashlib.sha256(ruta.read_bytes()).hexdigest() == esperado
    with rutas[0][0].open(encoding="utf-8") as archivo:
        assert next(csv.reader(archivo))[0] == "ejecucion"
