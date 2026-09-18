"""Pruebas del banco; las mediciones controladas son ficticias y solo viven en tmp_path."""

from __future__ import annotations

import csv
import hashlib
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from benchmarks.scripts import banco_pruebas as banco
from benchmarks.scripts import ejecutar_benchmarks as cli
from benchmarks.scripts.generar_datasets import TAMANOS, generar_dataset

SCRIPT = Path(cli.__file__)


def leer_csv(ruta):
    with ruta.open(encoding="utf-8", newline="") as archivo:
        lector = csv.DictReader(archivo)
        return lector.fieldnames, list(lector)


@pytest.fixture(scope="module")
def datasets(tmp_path_factory):
    carpeta = tmp_path_factory.mktemp("entradas_banco")
    return {n: banco.cargar_dataset(generar_dataset(n, carpeta), n) for n in TAMANOS}


@pytest.fixture
def control(monkeypatch):
    """Operacion de prueba con eventos observables, nunca usada por la CLI real."""
    eventos, rutas, estados = [], [], []
    monkeypatch.setattr(banco, "registrar_entorno", lambda: {"prueba_unitaria": True})

    def crear(fallo=None):
        @contextmanager
        def preparar(directorio, dataset):
            assert not list(directorio.iterdir())
            rutas.append(directorio)
            estado = {"insertados": 0, "lecturas": 999, "escrituras": 999}
            estados.append(estado)

            def evento(nombre):
                eventos.append(nombre)
                if fallo == nombre:
                    raise RuntimeError(f"fallo en {nombre}")

            def ejecutar():
                evento("ejecutar")
                assert estado["insertados"] == 0
                assert estado["lecturas"] == estado["escrituras"] == 0
                estado.update(insertados=len(dataset.registros), lecturas=3, escrituras=7)

            def reiniciar():
                evento("reiniciar")
                estado.update(lecturas=0, escrituras=0)

            def contadores():
                evento("contadores")
                return estado["lecturas"], estado["escrituras"]

            def espacio():
                evento("espacio")
                return 28 * estado["insertados"], 0

            def validar():
                evento("validar")
                assert estado["insertados"] == len(dataset.registros)
                estado["lecturas"] += 1000

            try:
                evento("preparar")
                yield banco.Operacion(
                    len(dataset.registros),
                    ejecutar,
                    reiniciar,
                    contadores,
                    espacio,
                    validar,
                    {"tipo": "solo_prueba_unitaria"},
                )
            finally:
                evento("cerrar")

        return banco.Caso("prueba", "ficticia", "insercion", preparar)

    return SimpleNamespace(crear=crear, eventos=eventos, rutas=rutas, estados=estados)


@pytest.mark.parametrize("tamano", TAMANOS)
def test_carga_los_tres_datasets(datasets, tamano):
    dataset = datasets[tamano]
    assert len(dataset.registros) == tamano
    assert {fila[0] for fila in dataset.registros} == set(range(1, tamano + 1))
    assert all(isinstance(fila[2], float) for fila in dataset.registros)
    assert dataset.sha256 == hashlib.sha256(dataset.ruta.read_bytes()).hexdigest()
    with dataset.ruta.open(encoding="utf-8", newline="") as archivo:
        filas = list(csv.reader(archivo))[1:]
    assert dataset.registros == tuple((int(c), n, float(p)) for c, n, p in filas)


def test_dataset_inexistente(tmp_path):
    with pytest.raises(ValueError, match="dataset inexistente.*generar_datasets"):
        banco.cargar_dataset(tmp_path / "ausente.csv", 1000)


@pytest.mark.parametrize(
    "contenido, mensaje",
    [
        ("id,nombre,promedio\n", "cabecera"),
        ("codigo,nombre,promedio\n1,ana\n", "tres columnas"),
        ("codigo,nombre,promedio\nx,ana,10\n", "no numerico"),
        ("codigo,nombre,promedio\n1,ana,10\n1,beto,10\n", "duplicado"),
        ("codigo,nombre,promedio\n0,ana,10\n", "fuera de 1"),
        ("codigo,nombre,promedio\n1001,ana,10\n", "fuera de 1"),
        ("codigo,nombre,promedio\n1,abcdefghijklmnopq,10\n", "VARCHAR"),
        ("codigo,nombre,promedio\n1,ááááááááá,10\n", "VARCHAR"),
        ("codigo,nombre,promedio\n1,a\0b,10\n", "VARCHAR"),
        ("codigo,nombre,promedio\n1,ana,NaN\n", "promedio fuera"),
        ("codigo,nombre,promedio\n1,ana,inf\n", "promedio fuera"),
        ("codigo,nombre,promedio\n1,ana,20.01\n", "promedio fuera"),
        ("codigo,nombre,promedio\n1,ana,-0.01\n", "promedio fuera"),
        ("codigo,nombre,promedio\n1,ana,10\n", "se esperaban 1000"),
    ],
)
def test_dataset_invalido(tmp_path, contenido, mensaje):
    ruta = tmp_path / "invalido.csv"
    ruta.write_text(contenido, encoding="utf-8")
    with pytest.raises(ValueError, match=mensaje):
        banco.cargar_dataset(ruta, 1000)


def test_rechaza_utf8_invalido_y_tamano_no_soportado(tmp_path):
    ruta = tmp_path / "invalido.csv"
    ruta.write_bytes(b"\xff")
    with pytest.raises(ValueError, match="dataset invalido"):
        banco.cargar_dataset(ruta, 1000)
    with pytest.raises(ValueError, match="tamano no soportado"):
        banco.cargar_dataset(ruta, 12)


@pytest.mark.parametrize("calentamientos,repeticiones", [(1, 5), (0, 2), (2, 3)])
def test_repeticiones_independientes_y_limpieza(
    control, datasets, tmp_path, calentamientos, repeticiones
):
    rutas = banco.ejecutar_banco(
        [control.crear()],
        [datasets[1000]],
        salida=tmp_path / "resultados",
        temporales=tmp_path,
        calentamientos=calentamientos,
        repeticiones=repeticiones,
    )
    _, filas = leer_csv(rutas["mediciones"])
    _, resumen = leer_csv(rutas["resumen"])
    assert len(control.estados) == calentamientos + repeticiones
    assert len({id(estado) for estado in control.estados}) == len(control.estados)
    assert len(set(control.rutas)) == len(control.rutas)
    assert all(not ruta.exists() for ruta in control.rutas)
    assert [int(fila["repeticion"]) for fila in filas] == list(range(1, repeticiones + 1))
    assert resumen[0]["repeticiones"] == str(repeticiones)
    assert all(fila["paginas_leidas"] == "3" for fila in filas)
    assert all(fila["paginas_escritas"] == "7" for fila in filas)
    assert all(fila["espacio_antes_bytes"] == "0" for fila in filas)


def test_mediana_sin_calentamiento_ni_eliminar_outliers(control, datasets, tmp_path, monkeypatch):
    tiempos = iter([0, 9999, 0, 10, 0, 20, 0, 900, 0, 30, 0, 40])
    monkeypatch.setattr(banco.time, "perf_counter_ns", lambda: next(tiempos))
    rutas = banco.ejecutar_banco([control.crear()], [datasets[1000]], salida=tmp_path)
    _, filas = leer_csv(rutas["mediciones"])
    _, resumen = leer_csv(rutas["resumen"])
    assert [int(fila["tiempo_ns"]) for fila in filas] == [10, 20, 900, 30, 40]
    assert resumen[0]["mediana_tiempo_ns"] == "30"
    assert resumen[0]["min_tiempo_ns"] == "10"
    assert resumen[0]["max_tiempo_ns"] == "900"


def test_limites_del_cronometro_y_captura_inmediata(control, datasets, tmp_path, monkeypatch):
    tiempos = iter([100, 140])

    def reloj():
        control.eventos.append("reloj")
        return next(tiempos)

    monkeypatch.setattr(banco.time, "perf_counter_ns", reloj)
    banco.ejecutar_banco(
        [control.crear()],
        [datasets[1000]],
        salida=tmp_path,
        calentamientos=0,
        repeticiones=1,
    )
    assert control.eventos == [
        "preparar",
        "espacio",
        "reiniciar",
        "reloj",
        "ejecutar",
        "reloj",
        "contadores",
        "espacio",
        "validar",
        "cerrar",
    ]


@pytest.mark.parametrize("fallo", ["preparar", "ejecutar", "contadores", "validar", "cerrar"])
def test_limpia_temporales_ante_errores(control, datasets, tmp_path, fallo):
    salida = tmp_path / "resultados"
    with pytest.raises(RuntimeError, match=f"fallo en {fallo}"):
        banco.ejecutar_banco(
            [control.crear(fallo)],
            [datasets[1000]],
            salida=salida,
            temporales=tmp_path,
            calentamientos=0,
            repeticiones=1,
        )
    assert control.eventos[-1] == "cerrar"
    assert all(not ruta.exists() for ruta in control.rutas)
    assert not salida.exists()


def test_formato_csv_agrupacion_y_entorno(control, datasets, tmp_path):
    caso = control.crear()
    segundo = replace(caso, nombre="otra_prueba", tecnica="otra_ficticia")
    rutas = banco.ejecutar_banco(
        [caso, segundo],
        list(datasets.values()),
        salida=tmp_path,
        calentamientos=0,
        repeticiones=2,
        notas={"compilacion_core": "declaracion_de_prueba"},
    )
    cabecera, filas = leer_csv(rutas["mediciones"])
    assert cabecera == (
        "ejecucion,caso,tecnica,operacion,n_registros,n_operaciones,repeticion,tiempo_ns,"
        "paginas_leidas,paginas_escritas,datos_bytes,indices_bytes,espacio_antes_bytes,"
        "espacio_despues_bytes,estado"
    ).split(",")
    assert len(filas) == 12
    cabecera, resumen = leer_csv(rutas["resumen"])
    assert cabecera == (
        "ejecucion,caso,tecnica,operacion,n_registros,n_operaciones,repeticiones,"
        "mediana_tiempo_ns,mediana_paginas_leidas,mediana_paginas_escritas,mediana_datos_bytes,"
        "mediana_indices_bytes,mediana_espacio_antes_bytes,mediana_espacio_despues_bytes,"
        "min_tiempo_ns,max_tiempo_ns"
    ).split(",")
    assert len(resumen) == 6
    assert {(fila["caso"], int(fila["n_registros"])) for fila in resumen} == {
        (nombre, tamano) for nombre in ("prueba", "otra_prueba") for tamano in TAMANOS
    }
    cabecera, filas_entorno = leer_csv(rutas["entorno"])
    assert cabecera == ["ejecucion", "clave", "valor"]
    entorno = {fila["clave"]: fila["valor"] for fila in filas_entorno}
    assert entorno["declarado.compilacion_core"] == "declaracion_de_prueba"
    assert entorno["dataset.1000.sha256"] == datasets[1000].sha256
    for ruta in rutas.values():
        assert b"\r" not in ruta.read_bytes()
        assert ruta.read_bytes().endswith(b"\n")


def test_no_sobrescribe_ejecuciones(control, datasets, tmp_path):
    primera = banco.ejecutar_banco(
        [control.crear()],
        [datasets[1000]],
        salida=tmp_path,
        calentamientos=0,
        repeticiones=1,
    )
    originales = {ruta: ruta.read_bytes() for ruta in primera.values()}
    segunda = banco.ejecutar_banco(
        [control.crear()],
        [datasets[1000]],
        salida=tmp_path,
        calentamientos=0,
        repeticiones=1,
    )
    assert set(primera.values()).isdisjoint(segunda.values())
    assert len(list(tmp_path.glob("*.csv"))) == 6
    with pytest.raises(FileExistsError):
        banco.escribir_csv(primera["mediciones"], banco.CABECERA_MEDICIONES, [])
    assert all(ruta.read_bytes() == contenido for ruta, contenido in originales.items())


@pytest.mark.parametrize("opciones", [{"calentamientos": -1}, {"repeticiones": 0}])
def test_rechaza_configuracion_invalida(control, datasets, tmp_path, opciones):
    with pytest.raises(ValueError, match="calentamientos.*repeticiones"):
        banco.ejecutar_banco([control.crear()], [datasets[1000]], salida=tmp_path, **opciones)
    assert not control.rutas
    assert not list(tmp_path.iterdir())


def test_entorno_no_inventa_compilacion_y_tolera_git_ausente(monkeypatch):
    def sin_git(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(banco.subprocess, "run", sin_git)
    entorno = banco.registrar_entorno()
    assert entorno["python"] == banco.platform.python_version()
    assert entorno["compilacion_core"] == "no_documentado"
    assert entorno["git_commit"] == "no_disponible"
    assert entorno["cache_so"] == "no_controlada"


@pytest.mark.parametrize("page_size", [None, 512])
def test_adaptador_heap_usa_metadata_y_archivo_real(datasets, tmp_path, page_size):
    # Doble del binding solo para verificar llamadas; no se exportan benchmarks con el.
    tabla = Mock()
    tabla.stats.return_value = SimpleNamespace(pages_read=17, pages_written=23)
    tabla.size.return_value = 1000
    tabla.scan.return_value = datasets[1000].registros
    db = Mock()
    db.create_table.return_value = tabla
    real = 8192 if page_size is None else page_size
    db.table_info.return_value = SimpleNamespace(file="alumnos.heap", page_size=real)
    (tmp_path / "alumnos.heap").write_bytes(b"archivo de prueba")
    nativo = SimpleNamespace(
        Database=Mock(return_value=db),
        Schema=Mock(),
        Column=Mock(),
        DataType=SimpleNamespace(INT="int", VARCHAR="varchar", DOUBLE="double"),
        kind=SimpleNamespace(HEAP="heap"),
        __file__="binding_de_prueba",
    )
    caso = cli.caso_insercion_heap(nativo, page_size)
    with caso.preparar(tmp_path, datasets[1000]) as operacion:
        assert operacion.configuracion["page_size"] == real
        assert operacion.medir_espacio() == (len(b"archivo de prueba"), 0)
        assert operacion.capturar_contadores() == (17, 23)
        operacion.reiniciar_contadores()
        operacion.ejecutar()
        operacion.validar()
        assert tabla.insert.call_count == 1000
        assert db.flush.call_count == 2
        assert db.create_table.call_args.kwargs == ({} if page_size is None else {"page_size": 512})
        db.close.assert_not_called()
    db.close.assert_called_once_with("alumnos")
    tabla.reset_stats.assert_called_once()


def test_bindings_ausentes_producen_error_claro(monkeypatch):
    def sin_modulo(nombre):
        raise ModuleNotFoundError(nombre)

    monkeypatch.setattr(cli.importlib, "import_module", sin_modulo)
    with pytest.raises(RuntimeError, match="no se pueden cargar los bindings.*PYTHONPATH"):
        cli.cargar_bindings()


def test_cli_ayuda_y_dataset_ausente_fuera_del_repo(tmp_path):
    ayuda = subprocess.run(
        [sys.executable, "-B", str(SCRIPT), "--help"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "--calentamientos" in ayuda.stdout
    resultado = subprocess.run(
        [
            sys.executable,
            "-B",
            str(SCRIPT),
            "--datasets",
            str(tmp_path),
            "--salida",
            str(tmp_path / "salida"),
            "--tamanos",
            "1000",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert resultado.returncode == 1
    assert "dataset inexistente" in resultado.stderr
    assert not (tmp_path / "salida").exists()


def test_integracion_heap_real(datasets, tmp_path):
    nativo = pytest.importorskip(
        "quipudb_native",
        reason="requiere bindings compilados para este Python y configurados en PYTHONPATH",
    )
    rutas = banco.ejecutar_banco(
        [cli.caso_insercion_heap(nativo)],
        [datasets[1000]],
        salida=tmp_path / "resultados",
        temporales=tmp_path,
        calentamientos=1,
        repeticiones=2,
    )
    _, filas = leer_csv(rutas["mediciones"])
    assert len(filas) == 2
    for fila in filas:
        assert fila["tecnica"] == "heap"
        assert int(fila["tiempo_ns"]) > 0
        assert int(fila["paginas_leidas"]) > 0
        assert int(fila["paginas_escritas"]) > 0
        assert int(fila["datos_bytes"]) > int(fila["espacio_antes_bytes"]) > 0
        assert fila["indices_bytes"] == "0"
    _, entorno = leer_csv(rutas["entorno"])
    assert int(next(f["valor"] for f in entorno if f["clave"].endswith(".page_size"))) > 0
    assert not list(tmp_path.glob("quipudb_bench_*"))
