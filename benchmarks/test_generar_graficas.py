"""Pruebas con mediciones sinteticas; no ejecutan estructuras ni benchmarks."""

import copy
import csv
import hashlib
import json
from statistics import median

import pytest

from benchmarks.scripts import generar_graficas as g


@pytest.fixture
def datos():
    corridas = {}
    for issue, ident in g.IDS.items():
        mediciones, resumen = [], []
        for figura in g.FIGURAS:
            if figura.issue != issue:
                continue
            for tecnica in figura.tecnicas:
                for n in g.TAMANOS:
                    base = {
                        "ejecucion": ident,
                        "caso": f"{tecnica}_{figura.operacion}",
                        "tecnica": tecnica,
                        "operacion": figura.operacion,
                        "n_registros": str(n),
                        "n_operaciones": str(g.operaciones(figura.operacion, n)),
                    }
                    tiempos = (1_000_000, 2_000_000, 3_000_000, 4_000_000, 99_000_000)
                    muestras = []
                    for repeticion, tiempo in enumerate(tiempos, 1):
                        muestra = dict(
                            base,
                            repeticion=str(repeticion),
                            estado="ok",
                            **dict.fromkeys(g.METRICAS, "0"),
                        )
                        muestra.update(
                            tiempo_ns=str(tiempo),
                            datos_bytes="4096",
                            espacio_despues_bytes="4096",
                            paginas_leidas="1",
                        )
                        muestras.append(muestra)
                    mediciones.extend(muestras)
                    fila = dict(
                        base,
                        repeticiones="5",
                        min_tiempo_ns=str(min(tiempos)),
                        max_tiempo_ns=str(max(tiempos)),
                    )
                    fila.update(
                        {
                            f"mediana_{m}": str(median(int(r[m]) for r in muestras))
                            for m in g.METRICAS
                        }
                    )
                    resumen.append(fila)
        entorno = [
            {"ejecucion": ident, "clave": k, "valor": v}
            for k, v in {
                "calentamientos": "1",
                "repeticiones": "5",
                "declarado.proposito": f"experimento_oficial_issue_{issue}",
            }.items()
        ]
        corridas[issue] = {"mediciones": mediciones, "resumen": resumen, "entorno": entorno}
    return corridas


@pytest.fixture
def fuentes(tmp_path, datos):
    entrada = tmp_path / "fuentes_sinteticas"
    entrada.mkdir()
    manifiesto = {"corridas": {}}
    for issue, ident in g.IDS.items():
        fuente = {"ejecucion": ident, "archivos": {}}
        for tipo, cabecera in (
            ("mediciones", g.CABECERA_MEDICIONES),
            ("resumen", g.CABECERA_RESUMEN),
            ("entorno", g.CABECERA_ENTORNO),
        ):
            ruta = entrada / f"{ident}_{tipo}.csv"
            with ruta.open("w", newline="", encoding="utf-8") as archivo:
                writer = csv.DictWriter(archivo, fieldnames=cabecera)
                writer.writeheader()
                writer.writerows(datos[issue][tipo])
            fuente["archivos"][tipo] = {
                "nombre": ruta.name,
                "sha256": hashlib.sha256(ruta.read_bytes()).hexdigest(),
            }
        manifiesto["corridas"][issue] = fuente
    ruta_manifiesto = tmp_path / "fuentes.json"
    ruta_manifiesto.write_text(json.dumps(manifiesto), encoding="utf-8")
    return entrada, ruta_manifiesto


def test_validacion_y_muestras_altas(datos):
    for issue, cantidad in (("39", 18), ("40", 63)):
        resumenes, grupos = g.validar_corrida(issue, datos[issue])
        assert len(resumenes) == len(grupos) == cantidad
        assert sum(map(len, grupos.values())) == cantidad * 5
        assert all(r["mediana_tiempo_ns"] == "3000000" for r in resumenes.values())
        assert all(r["max_tiempo_ns"] == "99000000" for r in resumenes.values())
    assert len(g.FIGURAS) == 12
    assert sum(len(f.tecnicas) for f in g.FIGURAS if f.issue == "40") == 21
    assert all(
        "extendible_hash" not in f.tecnicas
        for f in g.FIGURAS
        if f.operacion in ("busqueda_rango", "recorrido_ordenado")
    )
    assert "bplus_clustered" not in next(
        f.tecnicas for f in g.FIGURAS if f.operacion == "construccion_indice"
    )


@pytest.mark.parametrize(
    ("tipo", "campo", "valor", "mensaje"),
    [
        ("mediciones", "tiempo_ns", "nan", "no finita"),
        ("mediciones", "tiempo_ns", "inf", "no finita"),
        ("mediciones", "tiempo_ns", "-1", "negativa"),
        ("mediciones", "tiempo_ns", "0", "positivo"),
        ("mediciones", "tiempo_ns", "texto", "no numerico"),
        ("mediciones", "estado", "error", "estado"),
        ("mediciones", "ejecucion", "validacion_pequena", "ejecucion"),
        ("mediciones", "caso", "hash_rango_falso", "no soportado"),
        ("mediciones", "n_registros", "2000", "no soportado"),
        ("mediciones", "n_operaciones", "0", "operaciones"),
        ("mediciones", "tecnica", "otra", "tecnica"),
        ("mediciones", "repeticion", "2", "repeticiones unicas"),
        ("mediciones", "espacio_despues_bytes", "42", "espacio total"),
        ("resumen", "mediana_tiempo_ns", "42", "mediana inconsistente"),
        ("resumen", "max_tiempo_ns", "42", "maximo inconsistente"),
        ("resumen", "repeticiones", "4", "cinco repeticiones"),
    ],
)
def test_rechaza_metricas_incorrectas(datos, tipo, campo, valor, mensaje):
    datos["39"][tipo][0][campo] = valor
    with pytest.raises(ValueError, match=mensaje):
        g.validar_corrida("39", datos["39"])


def test_incompletos_duplicados_y_entorno(datos):
    for cambio, mensaje in (
        ("falta", "incompletos"),
        ("duplicado", "duplicado"),
        ("entorno", "configuracion"),
    ):
        copia = copy.deepcopy(datos["39"])
        if cambio == "falta":
            copia["resumen"].pop()
        elif cambio == "duplicado":
            copia["resumen"].append(copia["resumen"][0])
        else:
            copia["entorno"][0]["valor"] = "2"
        with pytest.raises(ValueError, match=mensaje):
            g.validar_corrida("39", copia)


def test_unidades_tablas_y_separacion(datos):
    assert g.ns_a_ms(1_000_000) == 1
    assert g.bytes_a_mib(1_048_576) == 1
    tablas = g.tablas_resultados({i: g.validar_corrida(i, d) for i, d in datos.items()})
    assert "90 muestras; 18 resúmenes" in tablas
    assert "315 muestras; 63 resúmenes" in tablas
    assert "99.000000 ms" in tablas
    assert "3.000000 | 3.000000 | 3.000000" in tablas
    assert "hash_busqueda_rango" not in tablas


def test_csv_invalido():
    for contenido in (b"otra,cabecera\n1,2\n", b"ejecucion,clave,valor\n1,2\n", b"\xff"):
        with pytest.raises(ValueError):
            g.leer_csv(contenido, g.CABECERA_ENTORNO)


def test_fuentes_hash_y_faltante(fuentes):
    entrada, manifiesto = fuentes
    assert set(g.cargar_fuentes(entrada, manifiesto)) == {"39", "40"}
    ruta = entrada / f"{g.IDS['39']}_mediciones.csv"
    ruta.write_bytes(ruta.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="SHA-256"):
        g.cargar_fuentes(entrada, manifiesto)
    ruta.unlink()
    with pytest.raises(ValueError, match="inexistentes"):
        g.cargar_fuentes(entrada, manifiesto)


def test_cli_fuentes_ausentes(tmp_path, capsys):
    with pytest.raises(SystemExit) as error:
        g.main(["--entrada", str(tmp_path), "--salida", str(tmp_path / "salida")])
    assert error.value.code == 2
    assert "inexistentes" in capsys.readouterr().err
    assert not (tmp_path / "salida").exists()


def test_renderizado_completo_y_fuentes_intactas(fuentes, tmp_path):
    pytest.importorskip("matplotlib", reason="renderizado requiere Matplotlib")
    entrada, manifiesto = fuentes
    plantilla = tmp_path / "plantilla.md"
    plantilla.write_text(f"Informe de prueba sintetico\n{g.INICIO}\n{g.FIN}\n", encoding="utf-8")
    antes = {p: p.read_bytes() for p in entrada.iterdir()}
    salida = tmp_path / "salida"
    assert g.generar(entrada, salida, manifiesto, plantilla) == 30
    assert antes == {p: p.read_bytes() for p in entrada.iterdir()}
    assert len(list((salida / "graficas").glob("*.png"))) == 15
    assert len(list((salida / "graficas").glob("*.svg"))) == 15
    for ruta in (salida / "graficas").iterdir():
        assert ruta.stat().st_size > 1000
        if ruta.suffix == ".png":
            assert ruta.read_bytes().startswith(b"\x89PNG")
        else:
            assert "<svg" in ruta.read_text()
            assert all(linea == linea.rstrip() for linea in ruta.read_text().splitlines())
    informe = (salida / "comparacion_experimental.md").read_text()
    assert "99.000000 ms" in informe
    assert "315 muestras" in informe


def test_salida_peligrosa_y_plantilla_invalida(fuentes, tmp_path):
    entrada, manifiesto = fuentes
    with pytest.raises(ValueError, match="salida"):
        g.generar(entrada, tmp_path, manifiesto, tmp_path / "no_existe")
    plantilla = tmp_path / "plantilla.md"
    plantilla.write_text("sin marcadores")
    with pytest.raises(ValueError, match="plantilla"):
        g.generar(entrada, tmp_path / "salida", manifiesto, plantilla)


def test_contenido_visual_sin_recortes(datos):
    matplotlib = pytest.importorskip("matplotlib", reason="renderizado requiere Matplotlib")
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.collections import PathCollection

    corridas = {i: g.validar_corrida(i, d) for i, d in datos.items()}
    for figura in g.FIGURAS:
        fig = g.graficar_tiempo(plt, figura, *corridas[figura.issue])
        try:
            ax = fig.axes[0]
            assert ax.get_xscale() == ax.get_yscale() == "log"
            puntos = [c for c in ax.collections if isinstance(c, PathCollection)]
            assert sum(len(c.get_offsets()) for c in puntos) == 15 * len(figura.tecnicas)
            assert ax.get_ylim()[1] > 99  # La muestra alta sintetica no queda recortada.
        finally:
            plt.close(fig)
    for issue, incremental in (("39", False), ("40", False), ("40", True)):
        fig = g.graficar_espacio(plt, issue, corridas[issue][0], incremental)
        try:
            for ax in fig.axes:
                assert ax.get_ylim()[0] == 0
                assert all(b.get_y() + b.get_height() < ax.get_ylim()[1] for b in ax.patches)
            if issue == "39":
                assert "B+" not in fig._supxlabel.get_text()
        finally:
            plt.close(fig)
