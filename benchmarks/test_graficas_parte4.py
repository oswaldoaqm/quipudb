"""Comprueba coordenadas, exportacion PNG y proteccion de fuentes."""

import json
from pathlib import Path

import pytest

matplotlib = pytest.importorskip("matplotlib", reason="las figuras requieren Matplotlib")
mpimg = pytest.importorskip("matplotlib.image")
np = pytest.importorskip("numpy")

from benchmarks.scripts import graficas_parte4 as g
from benchmarks.scripts.datos_parte4 import huellas, leer_191, leer_192
from benchmarks.scripts.generar_graficas_parte4 import generar

pytest_plugins = ["benchmarks.test_datos_parte4"]


def test_graficas_coordenadas_y_png(fuentes, tmp_path, monkeypatch):
    a, b = fuentes
    salida = tmp_path / "figuras"
    salida.mkdir()
    monkeypatch.setattr(g, "MODALIDADES", ("audio",))
    monkeypatch.setattr(g, "TAMANOS", (1000, 1000, 1000))
    # Se inspeccionan los datos entregados al renderer; los paneles usan tres posiciones.
    capturas = []
    original = g.guardar

    def guardar(fig, salida, nombre):
        capturas.append(
            (nombre, [(list(l.get_xdata()), list(l.get_ydata())) for l in fig.axes[0].lines])
        )
        return original(fig, salida, nombre)

    monkeypatch.setattr(g, "guardar", guardar)
    g.estilo()
    d = leer_191(a)
    e = leer_192(b)
    files = (
        g.construccion(d, salida)
        + g.consultas(d, salida)
        + g.recall(d, salida)
        + g.metricas(e, salida)
    )
    for p in files:
        img = mpimg.imread(p)
        assert img.shape[1] >= 2400 and img.shape[0] >= 1500
        assert np.std(img[:, :, :3]) > 0.05
    build = next(v for n, v in capturas if n == "construccion_audio")
    assert build[0][1] == [0.3, 0.3, 0.3]
    consulta = next(v for n, v in capturas if n == "consulta_audio")
    assert consulta[0][1] == [6, 6, 6]
    curva = next(v for n, v in capturas if n == "recall_audio_1000")
    assert curva[0][0] == [6, 9, 15, 27, 51, 99]
    metric = next(v for n, v in capturas if n == "metricas_audio_ivf")
    assert metric[0][1] == [51, 51, 51]


def test_manifiesto_rechaza_cambios_antes_de_exportar(fuentes, tmp_path):
    a, b = fuentes
    p = tmp_path / "fuentes.json"
    p.write_text(json.dumps({"191": huellas(a), "192": huellas(b)}))
    (a / "resumen.csv").write_text("alterado")
    with pytest.raises(ValueError, match="resumen.csv"):
        generar(a, b, tmp_path / "salida", p)
    assert not (tmp_path / "salida").exists()


def test_figuras_publicadas_corresponden_a_fuentes_reales(tmp_path):
    raiz = Path(__file__).resolve().parents[1]
    a = raiz / "benchmarks/datasets/ivf_hnsw_191"
    b = raiz / "benchmarks/datasets/metricas_192"
    if not a.exists() or not b.exists():
        pytest.skip("fuentes locales ignoradas no disponibles")
    informe = raiz / "docs/informe"
    publicadas = informe / "graficas/multimedia"
    registro = json.loads((publicadas / "renderizado.json").read_text())
    archivos = generar(a, b, tmp_path / "figuras", informe / "fuentes_multimedia.json")
    assert len(archivos) == len({p.name for p in archivos}) == 14
    assert {p.name for p in archivos} == set(registro["figuras"])
    for p in archivos:
        imagen = mpimg.imread(p)
        assert imagen.shape[1] == 2520 and imagen.shape[0] in (1560, 1800)
        assert (publicadas / p.name).exists()
        # Otra version de Matplotlib puede cambiar el rasterizado, no los datos.
        if registro["matplotlib"] == matplotlib.__version__:
            assert p.read_bytes() == (publicadas / p.name).read_bytes()
