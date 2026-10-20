"""Pruebas de #174: consultas del GIN y generador de graficas, sin servidor ni CSV oficiales.

La integracion contra un PostgreSQL de verdad se salta salvo que la variable
QUIPUDB_DSN_GIN apunte a uno: el script borra y crea la tabla `documentos`, asi
que tiene que ser un servidor de prueba, como el cluster temporal del README.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os

import pytest

from benchmarks.scripts import bench_gin as gin
from benchmarks.scripts import generar_graficas_texto as gr
from benchmarks.scripts.banco_pruebas import CABECERA_ENTORNO, CABECERA_RESUMEN, escribir_csv
from benchmarks.scripts.cargar_documentos import ConsultaTexto
from benchmarks.scripts.casos_texto import casos_texto
from benchmarks.scripts.relevancia_texto import escribir_rankings, leer_rankings

# ---------------------------------------------------------------------------
# Consultas del GIN
# ---------------------------------------------------------------------------


def test_and_es_plainto_tsquery_y_or_cambia_cada_and():
    assert gin.tsquery_sql("gin_and") == "plainto_tsquery('english', %s)"
    o = gin.tsquery_sql("gin_or")
    assert o == "CAST(replace(plainto_tsquery('english', %s)::text, ' & ', ' | ') AS tsquery)"
    # El texto de la consulta va como parametro, nunca pegado al SQL.
    assert o.count("%s") == 1
    with pytest.raises(ValueError, match="modo desconocido"):
        gin.tsquery_sql("gin_xor")


def test_el_top_ordena_por_ts_rank_y_desempata_por_id():
    sql = gin.sql_top("gin_or")
    assert "ts_rank(tsv, q)" in sql
    assert "WHERE tsv @@ q" in sql
    assert sql.endswith("ORDER BY puntaje DESC, id LIMIT 10")
    assert gin.sql_top("gin_and", None).endswith("ORDER BY puntaje DESC, id")


def test_el_gin_mide_los_mismos_casos_de_consulta_que_quipudb():
    class NativoFalso:
        pass

    consultas = tuple(
        ConsultaTexto(i, longitud, "x") for i, longitud in enumerate(("1", "3", "5+"), start=1)
    )
    quipudb = {c.operacion for c in casos_texto(NativoFalso(), consultas, 1)}
    quipudb.discard("construccion_indice")
    # Los nombres que arma el generador para cada tecnica son los del banco.
    for tecnica in (*gin.MODOS, "tf_idf", "bm25"):
        for longitud in gr.LONGITUDES:
            caso = gr.caso_consulta(tecnica, longitud)
            assert caso.startswith(f"{tecnica}_")
            assert caso.removeprefix(f"{tecnica}_") in quipudb


def test_la_sesion_apaga_jit_y_paralelismo():
    assert gin.AJUSTES_SESION == {"jit": "off", "max_parallel_workers_per_gather": "0"}


# ---------------------------------------------------------------------------
# Generador de graficas y tablas
# ---------------------------------------------------------------------------

TECNICAS = ("tf_idf", "bm25", "gin_or", "gin_and")


def _fila(caso, tecnica, n, tiempo_ns, paginas=10, datos=4096, indices=8192, operaciones=100):
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
    """Tres corridas y los datasets sinteticos, con todo lo que el generador lee.

    Una consulta por longitud (1, 2, 3); la relevante de la consulta c es el
    documento c. TF-IDF y BM25 lo ponen primero; los GIN no lo encuentran.
    """
    entrada, datasets = tmp_path / "results", tmp_path / "datasets"
    entrada.mkdir()
    datasets.mkdir()
    with (datasets / "consultas.csv").open("w", encoding="utf-8", newline="") as f:
        escritor = csv.writer(f, lineterminator="\n")
        escritor.writerow(("id", "longitud", "pregunta", "texto"))
        escritor.writerows(
            [(1, "1", "9", "oro"), (2, "3", "9", "a b c"), (3, "5+", "9", "a b c d e")]
        )
    for n in gr.TAMANOS:
        with (datasets / f"relevancia_{n}.csv").open("w", encoding="utf-8", newline="") as f:
            f.write("consulta,documento\n1,1\n2,2\n3,3\n")

    resumenes = {"172": [], "174": []}
    entornos = {"172": [], "173": [], "174": []}
    rankings = {"173": [], "174": []}
    for n in gr.TAMANOS:
        resumenes["172"].append(
            _fila("spimi_construccion_indice", "spimi", n, 2e6 * n / 1000, operaciones=1)
        )
        resumenes["174"].append(
            _fila("gin_construccion_indice", "gin", n, 5e5 * n / 1000, operaciones=1)
        )
        resumenes["174"].append(_fila("gin_tokenizacion", "gin", n, 1e6 * n / 1000, operaciones=1))
        entornos["174"].append(
            {"ejecucion": "s", "clave": f"tabla.{n}.tsvector_bytes", "valor": "1024"}
        )
        for longitud in gr.LONGITUDES:
            for i, tecnica in enumerate(TECNICAS):
                corrida = "174" if tecnica.startswith("gin") else "172"
                caso = gr.caso_consulta(tecnica, longitud)
                resumenes[corrida].append(_fila(caso, tecnica, n, (i + 1) * 1e6))
                entornos[corrida].append(
                    {
                        "ejecucion": "s",
                        "clave": f"caso.{caso}.{n}.coincidentes_por_consulta",
                        "valor": "250",
                    }
                )
        for consulta in (1, 2, 3):
            for tecnica in TECNICAS:
                corrida = "174" if tecnica.startswith("gin") else "173"
                documentos = [consulta, 50] if corrida == "173" else [60, 70]
                for posicion, documento in enumerate(documentos, start=1):
                    rankings[corrida].append(
                        (tecnica, n, consulta, posicion, documento, 3.0 - posicion)
                    )

    manifiesto = {"corridas": {}, "datasets": {}}
    for corrida, tipos in (
        ("172", ("mediciones", "resumen", "entorno")),
        ("173", ("rankings", "entorno")),
        ("174", ("mediciones", "resumen", "entorno", "rankings")),
    ):
        archivos = {}
        for tipo in tipos:
            ruta = entrada / f"c{corrida}_{tipo}.csv"
            if tipo == "rankings":
                escribir_rankings(ruta, rankings[corrida])
            elif tipo == "resumen":
                escribir_csv(ruta, CABECERA_RESUMEN, resumenes[corrida])
            elif tipo == "entorno":
                escribir_csv(ruta, CABECERA_ENTORNO, entornos[corrida])
            else:
                escribir_csv(ruta, ("x",), [])
            archivos[tipo] = {"nombre": ruta.name, "sha256": _sha(ruta)}
        manifiesto["corridas"][corrida] = {"archivos": archivos}
    for ruta in datasets.iterdir():
        manifiesto["datasets"][ruta.name] = _sha(ruta)
    ruta_manifiesto = tmp_path / "fuentes.json"
    ruta_manifiesto.write_text(json.dumps(manifiesto), encoding="utf-8")
    informe = tmp_path / "informe.md"
    informe.write_text(f"antes\n{gr.INICIO}\nviejo\n{gr.FIN}\ndespues\n", encoding="utf-8")
    return entrada, datasets, ruta_manifiesto, informe


def _sha(ruta) -> str:
    return hashlib.sha256(ruta.read_bytes()).hexdigest()


def test_las_tablas_salen_de_las_fuentes(fuentes):
    entrada, datasets, manifiesto, _ = fuentes
    bloque = gr.tablas(gr.cargar(entrada, datasets, manifiesto))
    # 1, 2, 3 y 4 ms por lote de 100: 0,010 a 0,040 ms por consulta; GIN OR / BM25 = 1,5.
    assert "| 1 | 1 000 | 0,010 | 0,020 | 0,030 | 0,040 | 1,500 |" in bloque
    # Paginas: 10 por lote de 100 -> 0,1 por consulta, 4 KiB en QuipuDB y 8 en PostgreSQL.
    assert "| 5+ | 0 | 1 | 1 | 250 | 250 | 250 |" in bloque
    # Relevancia: QuipuDB encuentra el relevante primero (P@10 0,1, R@10 1); el GIN, no.
    assert "| 3 | 100 000 | 0,100 | 0,100 | 0,000 | 0,000 |" in bloque
    assert "| 3 | 100 000 | 1,000 | 1,000 | 0,000 | 0,000 |" in bloque
    # Una sola consulta distinta por grupo no es evidencia: p = 1.
    assert "| 1 | 1 000 | +1,000 | 1,000 | +1,000 | 1,000 |" in bloque
    assert "| 100 000 | 200,0 | 50,0 | 100,0 | 4 | 8 | 4 | 1 | 8 |" in bloque


def test_un_hash_distinto_aborta_antes_de_escribir(fuentes, tmp_path):
    entrada, datasets, manifiesto, informe = fuentes
    with (datasets / "relevancia_1000.csv").open("a", encoding="utf-8") as archivo:
        archivo.write("1,7\n")
    with pytest.raises(ValueError, match="SHA-256"):
        gr.generar(entrada, datasets, tmp_path / "graficas", manifiesto, informe)
    assert "viejo" in informe.read_text(encoding="utf-8")
    assert not (tmp_path / "graficas").exists()


def test_falta_una_fuente(fuentes, tmp_path):
    entrada, datasets, manifiesto, informe = fuentes
    (entrada / "c174_rankings.csv").unlink()
    with pytest.raises(ValueError, match="falta la fuente oficial"):
        gr.generar(entrada, datasets, tmp_path / "graficas", manifiesto, informe)


def test_genera_las_figuras_y_conserva_el_texto_fuera_de_los_marcadores(fuentes, tmp_path):
    pytest.importorskip("matplotlib", reason="renderizado requiere Matplotlib")
    entrada, datasets, manifiesto, informe = fuentes
    escritas = gr.generar(entrada, datasets, tmp_path / "graficas", manifiesto, informe)
    assert len(escritas) == 2 * len(gr.FIGURAS)
    assert all(ruta.stat().st_size > 0 for ruta in escritas)
    texto = informe.read_text(encoding="utf-8")
    assert texto.startswith("antes\n") and texto.endswith("despues\n")
    assert "viejo" not in texto


def test_el_manifiesto_oficial_apunta_a_las_tres_corridas():
    fuentes = json.loads(gr.MANIFIESTO.read_text(encoding="utf-8"))
    assert set(fuentes["corridas"]) == {"172", "173", "174"}
    assert set(fuentes["corridas"]["173"]["archivos"]) == {"rankings", "entorno"}
    assert set(fuentes["corridas"]["174"]["archivos"]) == {
        "mediciones",
        "resumen",
        "entorno",
        "rankings",
    }
    for corrida in fuentes["corridas"].values():
        for archivo in corrida["archivos"].values():
            assert archivo["nombre"].startswith(corrida["ejecucion"])
            assert len(archivo["sha256"]) == 64
    assert set(fuentes["datasets"]) == {
        "consultas.csv",
        *(f"relevancia_{n}.csv" for n in gr.TAMANOS),
    }


def test_las_fuentes_oficiales_coinciden_si_estan_presentes():
    fuentes = json.loads(gr.MANIFIESTO.read_text(encoding="utf-8"))
    rutas = [
        (gr.ENTRADA / a["nombre"], a["sha256"])
        for c in fuentes["corridas"].values()
        for a in c["archivos"].values()
    ] + [(gr.DATASETS / nombre, sha) for nombre, sha in fuentes["datasets"].items()]
    if not all(ruta.is_file() for ruta, _ in rutas):
        pytest.skip("los CSV oficiales estan fuera de Git; copialos a benchmarks/results")
    for ruta, esperado in rutas:
        assert _sha(ruta) == esperado


# ---------------------------------------------------------------------------
# Contra un PostgreSQL de prueba
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    "QUIPUDB_DSN_GIN" not in os.environ,
    reason="define QUIPUDB_DSN_GIN con un PostgreSQL de prueba (borra la tabla documentos)",
)
def test_integracion_gin_real(tmp_path):
    from benchmarks.scripts.cargar_documentos import leer_consultas, leer_documentos
    from benchmarks.scripts.generar_documentos import generar, leer_quora
    from benchmarks.test_generar_documentos import _zip_sintetico

    raiz = tmp_path / "datos"
    generar(leer_quora(_zip_sintetico(tmp_path / "quora.zip")), raiz, (1_000,), preguntas=20)
    conexion = gin.conectar(os.environ["QUIPUDB_DSN_GIN"])
    with conexion:
        rutas = gin.ejecutar(
            conexion,
            [leer_documentos(raiz / "documentos_1000.csv", 1_000)],
            leer_consultas(raiz / "consultas.csv"),
            salida=tmp_path / "res",
            calentamientos=0,
            repeticiones=1,
            cantidad=10,
        )
    # ejecutar ya verifico que cada plan use el GIN y que las respuestas se repitan.
    rankings = leer_rankings(rutas["rankings"])
    assert set(rankings) == {("gin_or", 1_000), ("gin_and", 1_000)}
    with rutas["entorno"].open(encoding="utf-8") as archivo:
        entorno = {fila["clave"]: fila["valor"] for fila in csv.DictReader(archivo)}
    for longitud in ("1", "3", "5omas"):
        o = float(
            entorno[f"caso.gin_or_consulta_{longitud}_palabras.1000.coincidentes_por_consulta"]
        )
        y = float(
            entorno[f"caso.gin_and_consulta_{longitud}_palabras.1000.coincidentes_por_consulta"]
        )
        # Exigir todos los terminos nunca encuentra mas que exigir alguno.
        assert o >= y
    assert entorno["postgres.jit"] == "off"
