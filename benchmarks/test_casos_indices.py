"""Contratos #40 con dobles en temporales e integracion real limitada a 1k."""

from __future__ import annotations

import csv
import random
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from benchmarks.scripts import banco_pruebas as banco
from benchmarks.scripts import casos_indices as indices
from benchmarks.scripts import ejecutar_benchmarks as cli
from benchmarks.scripts.casos_archivos import seleccionar_claves
from benchmarks.scripts.generar_datasets import TAMANOS, generar_dataset

CASOS = tuple((caso.tecnica, caso.operacion) for caso in indices.casos_indices(None))


def leer_csv(ruta):
    with ruta.open(encoding="utf-8", newline="") as entrada:
        return list(csv.DictReader(entrada))


@pytest.fixture(scope="module")
def dataset(tmp_path_factory):
    ruta = generar_dataset(1000, tmp_path_factory.mktemp("datos_indices"))
    return banco.cargar_dataset(ruta, 1000)


@pytest.fixture
def doble(monkeypatch):
    """Mocks de contratos con mapas de respuestas; no modelan algoritmos ni rendimiento."""
    eventos, instancias = [], []
    monkeypatch.setattr(banco, "registrar_entorno", lambda: {"solo_prueba_unitaria": True})

    def crear(fallo=None):
        def evento(nombre, *valores):
            eventos.append((nombre, *valores))
            if fallo == nombre:
                raise RuntimeError(f"fallo en {nombre}")

        def abrir(catalogo):
            evento("abrir")
            filas, entradas = {}, {}
            rid_actual = 0
            rutas = [catalogo.parent / "datos.reales", catalogo.parent / "indice.real"]
            paginas = {"tabla": [0, 0], "indice": [0, 0]}
            db, tabla, indice = Mock(), Mock(), Mock()
            tipo_tabla = None
            tiene_indice = False

            def crear_tabla(esquema, tipo, **opciones):
                nonlocal tipo_tabla
                evento("create_table", tipo)
                tipo_tabla = tipo
                rutas[0].write_bytes(b"d" * 137)
                return tabla

            def insertar(fila):
                nonlocal rid_actual
                evento("tabla.insert", tuple(fila))
                assert fila[0] not in filas
                rid_actual += 1  # Fuerza un RID diferente al reinsertar una clave.
                filas[fila[0]] = (rid_actual, tuple(fila))
                paginas["tabla"][1] += 1
                return rid_actual

            def insertar_indice(clave, rid):
                evento("indice.insert", clave, rid)
                assert clave not in entradas
                assert filas[clave][0] == rid
                entradas[clave] = rid
                paginas["indice"][1] += 1

            def crear_indice(*args):
                nonlocal tiene_indice
                evento("create_index", *args)
                assert not tiene_indice
                tiene_indice = True
                rutas[1].write_bytes(b"i" * 53)
                for clave, (rid, _) in filas.items():
                    insertar_indice(clave, rid)
                paginas["tabla"][0] += len(filas)
                return indice

            def info(nombre):
                evento("table_info")
                return SimpleNamespace(
                    file=rutas[0].name,
                    page_size=8192,
                    indexes=[SimpleNamespace(name="por_codigo", file=rutas[1].name)]
                    if tiene_indice
                    else [],
                )

            def borrar(clave, secundario):
                grupo = "indice" if secundario else "tabla"
                evento(f"{grupo}.remove", clave)
                mapa = entradas if secundario else filas
                paginas[grupo][1] += 1
                return int(mapa.pop(clave, None) is not None)

            def leer(rid):
                evento("tabla.read", rid)
                paginas["tabla"][0] += 1
                return next((fila for r, fila in filas.values() if r == rid), None)

            def buscar(lo, hi, secundario, operacion):
                grupo = "indice" if secundario else "tabla"
                evento(f"{grupo}.{operacion}", lo, hi)
                paginas[grupo][0] += 1
                mapa = entradas if secundario else filas
                return [
                    mapa[c] if secundario else mapa[c][1] for c in sorted(mapa) if lo <= c <= hi
                ]

            def scan(secundario):
                grupo = "indice" if secundario else "tabla"
                evento(f"{grupo}.scan")
                paginas[grupo][0] += 777  # Detecta contaminacion de validaciones.
                if secundario:
                    return sorted(entradas.items())
                claves = sorted(filas) if tipo_tabla == "bplus_clustered" else filas
                return [filas[c][1] for c in claves]

            def reiniciar(grupo):
                evento(f"{grupo}.reset")
                paginas[grupo][:] = [0, 0]

            def stats(grupo):
                evento(f"{grupo}.stats", *paginas[grupo])
                return SimpleNamespace(
                    pages_read=paginas[grupo][0], pages_written=paginas[grupo][1]
                )

            db.create_table.side_effect = crear_tabla
            db.create_index.side_effect = crear_indice
            db.table_info.side_effect = info
            db.flush.side_effect = lambda: evento("flush")
            db.close.side_effect = lambda nombre: evento("close")
            tabla.insert.side_effect = insertar
            indice.insert.side_effect = insertar_indice
            tabla.remove.side_effect = lambda c: borrar(c, False)
            indice.remove.side_effect = lambda c: borrar(c, True)
            tabla.read.side_effect = leer
            tabla.search.side_effect = lambda c: buscar(c, c, False, "search")
            indice.search.side_effect = lambda c: buscar(c, c, True, "search")
            tabla.range_search.side_effect = lambda lo, hi: buscar(lo, hi, False, "range")
            indice.range_search.side_effect = lambda lo, hi: buscar(lo, hi, True, "range")
            tabla.scan.side_effect = lambda: scan(False)
            indice.scan.side_effect = lambda: scan(True)
            tabla.reset_stats.side_effect = lambda: reiniciar("tabla")
            indice.reset_stats.side_effect = lambda: reiniciar("indice")
            tabla.stats.side_effect = lambda: stats("tabla")
            indice.stats.side_effect = lambda: stats("indice")
            tabla.size.side_effect = lambda: len(filas)
            indice.size.side_effect = lambda: len(entradas)
            instancias.append(SimpleNamespace(db=db, tabla=tabla, indice=indice, rutas=rutas))
            return db

        return SimpleNamespace(
            Database=Mock(side_effect=abrir),
            Schema=Mock(),
            Column=Mock(),
            DataType=SimpleNamespace(INT="INT", VARCHAR="VARCHAR", DOUBLE="DOUBLE"),
            kind=SimpleNamespace(HEAP="heap"),
            __file__="solo_mock_unitario",
        )

    return SimpleNamespace(crear=crear, eventos=eventos, instancias=instancias)


def caso_de(nativo, tecnica, operacion):
    return next(
        c
        for c in indices.casos_indices(nativo, consultas=17, consultas_rango=7)
        if (c.tecnica, c.operacion) == (tecnica, operacion)
    )


@pytest.mark.parametrize("n", TAMANOS)
def test_rangos_reproducibles_independientes_y_selectividad(n):
    estado_global = random.getstate()
    rangos = indices.seleccionar_rangos(n, 100)
    indices.seleccionar_rangos(100000, 2)
    assert rangos == indices.seleccionar_rangos(n, 100)
    assert len(set(rangos)) == 100
    assert all(1 <= lo <= hi <= n and hi - lo + 1 == n // 100 for lo, hi in rangos)
    assert random.getstate() == estado_global


@pytest.mark.parametrize("cantidad", [0, -1, 992])
def test_rangos_invalidos(cantidad):
    with pytest.raises(ValueError, match="consultas-rango"):
        indices.seleccionar_rangos(1000, cantidad)


def test_altas_nuevas_reproducibles_sin_cambiar_dataset(dataset):
    antes = dataset.registros
    nuevas = indices.registros_adicionales(dataset)
    assert nuevas == indices.registros_adicionales(dataset)
    assert len(nuevas) == len({r[0] for r in nuevas}) == 100
    assert all(c > 1000 and len(nombre.encode()) <= 16 and 0 <= p <= 20 for c, nombre, p in nuevas)
    assert [r[0] for r in nuevas] != sorted(r[0] for r in nuevas)
    assert dataset.registros is antes


def test_matriz_21_sin_hash_rango_orden_ni_construccion_agrupada():
    assert len(CASOS) == len(set(CASOS)) == 21
    assert ("bplus_clustered", "construccion_indice") not in CASOS
    for operacion in ("busqueda_rango", "recorrido_ordenado"):
        assert ("extendible_hash", operacion) not in CASOS
        with pytest.raises(ValueError, match="no soporta"):
            indices.caso_consulta(None, "extendible_hash", operacion)
    with pytest.raises(ValueError, match="secundarios"):
        indices.caso_construccion(None, "bplus_clustered")


@pytest.mark.parametrize("tecnica,operacion", CASOS)
def test_contratos_con_banco(tecnica, operacion, doble, dataset, tmp_path, monkeypatch):
    def reloj():
        doble.eventos.append(("reloj",))
        return 100 * sum(e[0] == "reloj" for e in doble.eventos)

    monkeypatch.setattr(banco.time, "perf_counter_ns", reloj)
    rutas = banco.ejecutar_banco(
        [caso_de(doble.crear(), tecnica, operacion)],
        [dataset],
        salida=tmp_path / "salida",
        temporales=tmp_path,
        calentamientos=0,
        repeticiones=1,
    )
    inicio, fin = [i for i, e in enumerate(doble.eventos) if e[0] == "reloj"]
    medidos = doble.eventos[inicio + 1 : fin]
    nombres = [e[0] for e in medidos]
    secundario = tecnica != "bplus_clustered"
    instancia = doble.instancias[0]
    assert doble.eventos[fin + 1][0] == "tabla.stats"
    if secundario:
        assert doble.eventos[fin + 2][0] == "indice.stats"
    captura_fin = fin + 2 + int(secundario)
    assert any(e[0] == "tabla.scan" for e in doble.eventos[captura_fin:])
    assert not any(nombre.endswith("reset") or nombre == "table_info" for nombre in nombres)
    assert instancia.tabla.reset_stats.call_count == 1
    assert instancia.indice.reset_stats.call_count == int(
        secundario and operacion != "construccion_indice"
    )
    assert instancia.db.create_index.call_count == int(secundario)
    assert nombres.count("create_index") == int(operacion == "construccion_indice")
    assert [c.args[0] for c in instancia.tabla.insert.call_args_list[:1000]] == list(
        dataset.registros
    )
    assert nombres.count("flush") == int(
        operacion not in ("busqueda_igualdad", "busqueda_rango", "recorrido_ordenado")
    )
    if operacion == "busqueda_igualdad":
        busquedas = [e[1] for e in medidos if e[0].endswith(".search")]
        assert busquedas == list(seleccionar_claves(1000, 17))
        assert nombres.count("tabla.read") == (17 if secundario else 0)
    if operacion == "busqueda_rango":
        assert [(e[1], e[2]) for e in medidos if e[0].endswith(".range")] == list(
            indices.seleccionar_rangos(1000, 7)
        )
        assert nombres.count("tabla.read") == (70 if secundario else 0)
    if operacion == "recorrido_ordenado":
        assert nombres.count("indice.scan" if secundario else "tabla.scan") == 1
        assert nombres.count("tabla.read") == (1000 if secundario else 0)
    if operacion == "mantenimiento_mixto" and secundario:
        inserts = [e for e in doble.eventos if e[0] == "indice.insert"]
        vistos = {}
        for _, clave, rid in inserts:
            assert rid not in vistos.setdefault(clave, set())
            vistos[clave].add(rid)
        assert len(inserts) == 1300
    fila = leer_csv(rutas["mediciones"])[0]
    cantidades = {
        "carga_indexada": 1000,
        "construccion_indice": 1,
        "busqueda_igualdad": 17,
        "busqueda_rango": 7,
        "recorrido_ordenado": 1,
        "insercion_incremental": 100,
        "eliminacion_incremental": 100,
        "mantenimiento_mixto": 600,
    }
    assert fila["caso"] == f"{tecnica}_{operacion}"
    assert int(fila["n_operaciones"]) == cantidades[operacion]
    assert fila["tiempo_ns"] == "100"
    capturas = doble.eventos[fin + 1 : captura_fin]
    assert int(fila["paginas_leidas"]) == sum(e[1] for e in capturas)
    assert int(fila["paginas_escritas"]) == sum(e[2] for e in capturas)
    assert int(fila["datos_bytes"]) == 137
    assert int(fila["indices_bytes"]) == (53 if secundario else 0)
    assert int(fila["espacio_antes_bytes"]) == (
        190 if secundario and operacion != "construccion_indice" else 137
    )
    entorno = {f["clave"]: f["valor"] for f in leer_csv(rutas["entorno"])}
    assert entorno[f"caso.{tecnica}_{operacion}.1000.rango_nativo"] == str(
        tecnica != "extendible_hash"
    )
    assert entorno[f"caso.{tecnica}_{operacion}.1000.orden_nativo"] == str(
        tecnica != "extendible_hash"
    )
    assert entorno[f"caso.{tecnica}_{operacion}.1000.page_size"] == "8192"
    assert not instancia.rutas[0].parent.exists()


@pytest.mark.parametrize(
    "operacion,fallo",
    [
        ("carga_indexada", "create_index"),
        ("construccion_indice", "create_index"),
        ("carga_indexada", "indice.insert"),
        ("busqueda_igualdad", "tabla.read"),
        ("eliminacion_incremental", "indice.remove"),
        ("mantenimiento_mixto", "tabla.scan"),
    ],
)
def test_limpieza_ante_fallos(operacion, fallo, doble, dataset, tmp_path):
    with pytest.raises(RuntimeError, match=fallo):
        banco.ejecutar_banco(
            [caso_de(doble.crear(fallo), "bplus_unclustered", operacion)],
            [dataset],
            salida=tmp_path / "salida",
            temporales=tmp_path,
            calentamientos=0,
            repeticiones=1,
        )
    doble.instancias[0].db.close.assert_called_once_with("alumnos")
    assert not list(tmp_path.iterdir())


def test_repeticiones_independientes_y_no_sobrescritura(doble, dataset, tmp_path):
    caso = caso_de(doble.crear(), "extendible_hash", "mantenimiento_mixto")
    opciones = {
        "salida": tmp_path / "salida",
        "temporales": tmp_path,
        "calentamientos": 1,
        "repeticiones": 2,
    }
    primera = banco.ejecutar_banco([caso], [dataset], **opciones)
    contenido = primera["mediciones"].read_bytes()
    segunda = banco.ejecutar_banco([caso], [dataset], **opciones)
    assert primera != segunda and primera["mediciones"].read_bytes() == contenido
    assert len(doble.instancias) == 6
    assert len({i.rutas[0] for i in doble.instancias}) == 6
    assert len(leer_csv(primera["mediciones"])) == 2
    for instancia in doble.instancias:
        assert instancia.tabla.insert.call_count == 1300
        assert instancia.indice.insert.call_count == 1300
        assert instancia.tabla.remove.call_count == 300
        assert not instancia.rutas[0].parent.exists()


@pytest.mark.parametrize("operacion", ["busqueda_igualdad", "busqueda_rango", "recorrido_ordenado"])
def test_validacion_rechaza_respuestas_incorrectas(operacion, doble, dataset, tmp_path):
    caso = caso_de(doble.crear(), "bplus_unclustered", operacion)
    with caso.preparar(tmp_path, dataset) as op:
        doble.instancias[0].tabla.read.side_effect = lambda rid: None
        op.ejecutar()
        with pytest.raises(RuntimeError, match="respuesta incompleta"):
            op.validar()


@pytest.mark.parametrize("suite,cantidad", [("heap", 1), ("archivos", 6), ("indices", 21)])
def test_cli_compatible(suite, cantidad, dataset, monkeypatch):
    banco_mock = Mock(return_value={})
    monkeypatch.setattr(cli, "cargar_dataset", lambda ruta, n: dataset)
    monkeypatch.setattr(cli, "cargar_bindings", lambda: object())
    monkeypatch.setattr(cli, "ejecutar_banco", banco_mock)
    monkeypatch.setattr(sys, "argv", ["bench", "--suite", suite, "--tamanos", "1000"])
    cli.main()
    assert len(banco_mock.call_args.args[0]) == cantidad
    assert banco_mock.call_args.kwargs["calentamientos"] == 1
    assert banco_mock.call_args.kwargs["repeticiones"] == 5


@pytest.mark.parametrize(
    "argumentos",
    [["--consultas", "1001"], ["--consultas-rango", "0"], ["--consultas-rango", "992"]],
)
def test_cli_rechaza_cantidades_invalidas(argumentos, monkeypatch):
    monkeypatch.setattr(
        sys, "argv", ["bench", "--suite", "indices", "--tamanos", "1000", *argumentos]
    )
    with pytest.raises(SystemExit, match="2"):
        cli.main()


@pytest.mark.parametrize("tecnica,operacion", CASOS)
def test_integracion_real_1k(tecnica, operacion, dataset, tmp_path):
    nativo = pytest.importorskip(
        "quipudb_native", reason="requiere bindings compilados para este Python y PYTHONPATH"
    )
    rutas = banco.ejecutar_banco(
        [caso_de(nativo, tecnica, operacion)],
        [dataset],
        salida=tmp_path / "salida",
        temporales=tmp_path,
        calentamientos=0,
        repeticiones=1,
    )
    fila = leer_csv(rutas["mediciones"])[0]
    assert fila["estado"] == "ok"
    assert int(fila["tiempo_ns"]) > 0 and int(fila["datos_bytes"]) > 0
    assert (int(fila["indices_bytes"]) > 0) == (tecnica != "bplus_clustered")
    if operacion in ("busqueda_igualdad", "busqueda_rango", "recorrido_ordenado"):
        assert int(fila["paginas_leidas"]) > 0
        assert int(fila["paginas_escritas"]) == 0
    assert not list(tmp_path.glob("quipudb_bench_*"))
