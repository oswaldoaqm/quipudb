"""Contratos con mocks solo en tmp_path e integracion real pequena de los seis casos."""

from __future__ import annotations

import csv
import random
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from benchmarks.scripts import banco_pruebas as banco
from benchmarks.scripts import casos_archivos as archivos
from benchmarks.scripts import ejecutar_benchmarks as cli
from benchmarks.scripts.generar_datasets import SEMILLA, TAMANOS, generar_dataset

CASOS = (
    ("heap_insercion", "heap", "insercion", 1000),
    ("sequential_insercion", "sequential", "insercion", 1000),
    ("heap_busqueda_pk", "heap", "busqueda_pk", 17),
    ("sequential_busqueda_pk", "sequential", "busqueda_pk", 17),
    ("sequential_reorganizacion_explicita", "sequential", "reorganizacion_explicita", 1),
    (
        "sequential_eliminacion_con_reorganizacion",
        "sequential",
        "eliminacion_con_reorganizacion",
        1,
    ),
)


def leer_csv(ruta):
    with ruta.open(encoding="utf-8", newline="") as entrada:
        lector = csv.DictReader(entrada)
        return lector.fieldnames, list(lector)


@pytest.fixture(scope="module")
def dataset(tmp_path_factory):
    return banco.cargar_dataset(
        generar_dataset(1000, tmp_path_factory.mktemp("datos_archivos")), 1000
    )


@pytest.fixture
def doble(dataset, monkeypatch):
    """Respuestas guionadas: no emula algoritmos, paginas ni rendimiento del storage."""
    monkeypatch.setattr(banco, "registrar_entorno", lambda: {"solo_prueba_unitaria": True})
    eventos, instancias = [], []
    por_codigo = {fila[0]: fila for fila in dataset.registros}

    def crear(indice, fallo=None):
        reorganizacion = indice >= 4
        automatica = indice == 5
        claves = archivos.seleccionar_claves(1000, 301)
        excluidos = set(claves[: 300 + int(automatica)]) if reorganizacion else set()
        esperados = [fila for fila in dataset.registros if fila[0] not in excluidos]

        def registrar(nombre, valor=None):
            eventos.append(nombre)
            if fallo == nombre:
                raise RuntimeError(f"fallo en {nombre}")
            return valor

        def abrir(catalogo):
            registrar("abrir")
            archivo = catalogo.parent / "tabla.real"
            archivo.write_bytes(b"archivo real de la prueba")
            lecturas = 7

            def scan():
                nonlocal lecturas
                lecturas += 999
                return registrar("scan", esperados)

            tabla = Mock()
            tabla.insert.side_effect = lambda fila: registrar("insertar")
            tabla.search.side_effect = lambda clave: registrar("buscar", [por_codigo[clave]])
            tabla.remove.side_effect = lambda clave: registrar("eliminar", 1)
            tabla.scan.side_effect = scan
            tabla.size.side_effect = (
                [700, len(esperados)] if reorganizacion else lambda: len(esperados)
            )
            tabla.reset_stats.side_effect = lambda: registrar("reiniciar")
            tabla.stats.side_effect = lambda: registrar(
                "contadores", SimpleNamespace(pages_read=lecturas, pages_written=11)
            )
            db = Mock()
            db.create_table.return_value = tabla
            db.table_info.return_value = SimpleNamespace(file=archivo.name, page_size=8192)
            db.flush.side_effect = lambda: registrar("flush")
            db.close.side_effect = lambda nombre: registrar("cerrar")
            instancias.append(SimpleNamespace(db=db, tabla=tabla, archivo=archivo))
            return db

        ratios = iter([0.3, 0.0] * 20)
        return SimpleNamespace(
            Database=Mock(side_effect=abrir),
            Schema=Mock(),
            Column=Mock(),
            DataType=SimpleNamespace(INT="INT", VARCHAR="VARCHAR", DOUBLE="DOUBLE"),
            kind=SimpleNamespace(HEAP="heap", SEQUENTIAL="sequential"),
            __file__="solo_mock_de_prueba",
            wasted_ratio=Mock(side_effect=lambda tabla: registrar("desperdicio", next(ratios))),
            reorganize=Mock(side_effect=lambda tabla: registrar("reorganizar")),
        )

    return SimpleNamespace(crear=crear, eventos=eventos, instancias=instancias)


@pytest.mark.parametrize("n", TAMANOS)
def test_seleccion_y_porcentaje_independientes(n):
    estado_global = random.getstate()
    claves = archivos.seleccionar_claves(n, 1000)
    archivos.seleccionar_claves(100000, 3)
    assert claves == archivos.seleccionar_claves(n, 1000)
    assert len(claves) == len(set(claves)) == 1000
    assert all(1 <= clave <= n for clave in claves)
    assert random.getstate() == estado_global
    assert SEMILLA == 20260906
    assert archivos.cantidad_borrados(n) * 10 == n * 3
    assert (archivos.cantidad_borrados(n) + 1) / n > 0.3


@pytest.mark.parametrize("cantidad", [0, -1, 1001])
def test_consultas_invalidas(cantidad):
    with pytest.raises(ValueError, match="entre 1 y N"):
        archivos.seleccionar_claves(1000, cantidad)


@pytest.mark.parametrize("n", [0, -10, 999])
def test_porcentaje_inexacto_rechazado(n):
    with pytest.raises(ValueError, match="multiplo de 10"):
        archivos.cantidad_borrados(n)


def test_mismo_orden_csv_y_mismas_consultas(doble, dataset, tmp_path):
    for indice in range(4):
        carpeta = tmp_path / str(indice)
        carpeta.mkdir()
        caso = archivos.casos_archivos(doble.crear(indice), consultas=17)[indice]
        with caso.preparar(carpeta, dataset) as op:
            op.ejecutar()
            op.validar()
        tabla = doble.instancias[-1].tabla
        assert [llamada.args[0] for llamada in tabla.insert.call_args_list] == list(
            dataset.registros
        )
        if indice >= 2:
            assert [llamada.args[0] for llamada in tabla.search.call_args_list] == list(
                archivos.seleccionar_claves(1000, 17)
            )
    assert doble.instancias[2].tabla.search.call_args_list == (
        doble.instancias[3].tabla.search.call_args_list
    )


@pytest.mark.parametrize("indice", range(6), ids=[caso[0] for caso in CASOS])
def test_limites_medicion_metricas_y_csv(indice, doble, dataset, tmp_path, monkeypatch):
    nativo = doble.crear(indice)
    caso = archivos.casos_archivos(nativo, consultas=17)[indice]

    def reloj():
        doble.eventos.append("reloj")
        return 100 * doble.eventos.count("reloj")

    monkeypatch.setattr(banco.time, "perf_counter_ns", reloj)
    rutas = banco.ejecutar_banco(
        [caso],
        [dataset],
        salida=tmp_path / "salida",
        temporales=tmp_path,
        calentamientos=0,
        repeticiones=1,
    )
    inicio, fin = [i for i, evento in enumerate(doble.eventos) if evento == "reloj"]
    medidos = doble.eventos[inicio + 1 : fin]
    esperado = (
        ["insertar"] * 1000 + ["flush"]
        if indice < 2
        else ["buscar"] * 17
        if indice < 4
        else ["reorganizar", "flush"]
        if indice == 4
        else ["eliminar", "flush"]
    )
    assert medidos == esperado
    assert doble.eventos[inicio - 1] == "reiniciar"
    assert doble.eventos[fin + 1] == "contadores"
    assert "scan" not in doble.eventos[:fin]
    assert doble.eventos[-1] == "cerrar"
    if indice >= 4:
        assert doble.eventos[:inicio].count("eliminar") == 300
        assert doble.eventos[:inicio].count("desperdicio") == 1
        assert nativo.reorganize.call_count == int(indice == 4)
        tabla = doble.instancias[0].tabla
        assert len({llamada.args[0] for llamada in tabla.remove.call_args_list}) == (
            300 + int(indice == 5)
        )
    cabecera, filas = leer_csv(rutas["mediciones"])
    assert cabecera == list(banco.CABECERA_MEDICIONES)
    fila = filas[0]
    assert (fila["caso"], fila["tecnica"], fila["operacion"], int(fila["n_operaciones"])) == CASOS[
        indice
    ]
    assert fila["tiempo_ns"] == "100"
    assert fila["paginas_leidas"] == "7"  # No incluye el scan de validacion.
    assert fila["paginas_escritas"] == "11"
    for columna in ("datos_bytes", "espacio_antes_bytes", "espacio_despues_bytes"):
        assert int(fila[columna]) == len(b"archivo real de la prueba")
    assert fila["indices_bytes"] == "0"
    assert leer_csv(rutas["resumen"])[0] == list(banco.CABECERA_RESUMEN)
    assert leer_csv(rutas["entorno"])[0] == list(banco.CABECERA_ENTORNO)
    assert not doble.instancias[0].archivo.parent.exists()


@pytest.mark.parametrize("indice", [4, 5])
def test_rechaza_reorganizacion_prematura(indice, doble, dataset, tmp_path):
    nativo = doble.crear(indice)
    nativo.wasted_ratio.side_effect = None
    nativo.wasted_ratio.return_value = 0
    with (
        pytest.raises(RuntimeError, match="30%"),
        archivos.casos_archivos(nativo)[indice].preparar(tmp_path, dataset),
    ):
        pytest.fail("no debe entregar una operacion con un escenario incorrecto")
    nativo.reorganize.assert_not_called()
    doble.instancias[0].db.close.assert_called_once_with("alumnos")


@pytest.mark.parametrize("indice", [4, 5])
@pytest.mark.parametrize("error", ["desperdicio", "sobrevivientes"])
def test_rechaza_reorganizacion_incorrecta(indice, error, doble, dataset, tmp_path):
    nativo = doble.crear(indice)
    if error == "desperdicio":
        nativo.wasted_ratio.side_effect = [0.3, 0.1]
    with archivos.casos_archivos(nativo)[indice].preparar(tmp_path, dataset) as op:
        if error == "sobrevivientes":
            doble.instancias[-1].tabla.scan.side_effect = lambda: dataset.registros
        op.ejecutar()
        with pytest.raises(RuntimeError, match="desperdicio cero|registros esperados"):
            op.validar()


@pytest.mark.parametrize(
    "indice,fallo", [(2, "insertar"), (4, "eliminar"), (4, "reorganizar"), (5, "scan")]
)
def test_limpieza_ante_errores(indice, fallo, doble, dataset, tmp_path):
    caso = archivos.casos_archivos(doble.crear(indice, fallo))[indice]
    with pytest.raises(RuntimeError, match=f"fallo en {fallo}"):
        banco.ejecutar_banco(
            [caso],
            [dataset],
            salida=tmp_path / "salida",
            temporales=tmp_path,
            calentamientos=0,
            repeticiones=1,
        )
    doble.instancias[0].db.close.assert_called_once_with("alumnos")
    assert not list(tmp_path.iterdir())


def test_repeticiones_nuevas_y_calentamiento_excluido(doble, dataset, tmp_path):
    caso = archivos.casos_archivos(doble.crear(5))[5]
    rutas = banco.ejecutar_banco(
        [caso],
        [dataset],
        salida=tmp_path / "salida",
        temporales=tmp_path,
        calentamientos=1,
        repeticiones=2,
    )
    assert len(doble.instancias) == 3
    assert len({instancia.archivo for instancia in doble.instancias}) == 3
    for instancia in doble.instancias:
        assert instancia.tabla.insert.call_count == 1000
        assert instancia.tabla.remove.call_count == 301
        assert not instancia.archivo.parent.exists()
    assert [fila["repeticion"] for fila in leer_csv(rutas["mediciones"])[1]] == ["1", "2"]
    assert leer_csv(rutas["resumen"])[1][0]["repeticiones"] == "2"


@pytest.mark.parametrize("suite,cantidad", [("heap", 1), ("archivos", 6)])
def test_cli_preserva_caso_minimo_y_agrega_suite(suite, cantidad, dataset, monkeypatch):
    capturado = Mock(return_value={})
    monkeypatch.setattr(cli, "cargar_dataset", lambda ruta, n: dataset)
    monkeypatch.setattr(cli, "cargar_bindings", lambda: object())
    monkeypatch.setattr(cli, "ejecutar_banco", capturado)
    args = ["bench", "--tamanos", "1000"]
    if suite == "archivos":
        args += ["--suite", suite, "--consultas", "17"]
    monkeypatch.setattr(sys, "argv", args)
    cli.main()
    assert len(capturado.call_args.args[0]) == cantidad
    assert capturado.call_args.kwargs["calentamientos"] == 1
    assert capturado.call_args.kwargs["repeticiones"] == 5


@pytest.mark.parametrize("consultas", ["0", "1001"])
def test_cli_rechaza_consultas_invalidas(consultas, monkeypatch, capsys):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "bench",
            "--suite",
            "archivos",
            "--tamanos",
            "1000",
            "--consultas",
            consultas,
        ],
    )
    with pytest.raises(SystemExit, match="2"):
        cli.main()
    assert "consultas debe estar entre" in capsys.readouterr().err


@pytest.fixture
def nativo_real():
    return pytest.importorskip(
        "quipudb_native",
        reason="requiere bindings compilados para este Python y configurados en PYTHONPATH",
    )


@pytest.mark.parametrize("automatica", [False, True])
def test_integracion_umbral_real(nativo_real, dataset, tmp_path, automatica):
    """Observa directamente el estado nativo antes/despues, no infiere el umbral de un mock."""
    q = nativo_real
    with archivos._abrir_tabla(q, tmp_path, "sequential", None) as (db, tabla, archivo, _):
        archivos._insertar(db, tabla, dataset.registros)
        espacio_cargado = archivo.stat().st_size
        claves = archivos.seleccionar_claves(1000, 301)
        for clave in claves[:300]:
            assert tabla.remove(clave) == 1
        db.flush()
        assert q.wasted_ratio(tabla) == pytest.approx(0.3)
        assert tabla.size() == 700
        assert archivo.stat().st_size == espacio_cargado
        if automatica:
            assert tabla.remove(claves[300]) == 1
        else:
            q.reorganize(tabla)
        db.flush()
        excluidos = set(claves[: 300 + int(automatica)])
        assert q.wasted_ratio(tabla) == 0
        assert tabla.size() == 700 - int(automatica)
        assert sorted(tuple(fila) for fila in tabla.scan()) == sorted(
            fila for fila in dataset.registros if fila[0] not in excluidos
        )
        assert archivo.stat().st_size < espacio_cargado


def test_integracion_suite_real(nativo_real, dataset, tmp_path):
    rutas = banco.ejecutar_banco(
        archivos.casos_archivos(nativo_real, consultas=17),
        [dataset],
        salida=tmp_path / "salida",
        temporales=tmp_path,
        calentamientos=0,
        repeticiones=1,
    )
    assert set(rutas) == {"mediciones", "resumen", "entorno"}
    filas = leer_csv(rutas["mediciones"])[1]
    assert len(filas) == 6
    for fila, esperado in zip(filas, CASOS, strict=True):
        assert (
            fila["caso"],
            fila["tecnica"],
            fila["operacion"],
            int(fila["n_operaciones"]),
        ) == esperado
        assert int(fila["tiempo_ns"]) > 0
        assert int(fila["paginas_leidas"]) > 0
        assert int(fila["datos_bytes"]) > 0
        assert fila["estado"] == "ok"
        if fila["operacion"] != "busqueda_pk":
            assert int(fila["paginas_escritas"]) > 0
    entorno = {fila["clave"]: fila["valor"] for fila in leer_csv(rutas["entorno"])[1]}
    for nombre, _, _, _ in CASOS:
        assert entorno[f"caso.{nombre}.1000.modulo_nativo"] == nativo_real.__file__
    assert not list(tmp_path.glob("quipudb_bench_*"))
