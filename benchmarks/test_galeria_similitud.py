"""Renderizado y reproduccion de la galeria, sin benchmarks grandes."""

import json
import re
from pathlib import Path

import pytest

matplotlib = pytest.importorskip("matplotlib")
mpimg = pytest.importorskip("matplotlib.image")
np = pytest.importorskip("numpy")

from benchmarks.scripts.casos_galeria import huella_resultados, preparar
from benchmarks.scripts.generar_galeria_similitud import exportar, figura
from benchmarks.scripts.multimedia_dataset import sha256


def caso_de_prueba():
    vecinos = [
        {"id": i, "rid": [1, i], "nombre": f"{i:06d}.png", "distancia": i / 10} for i in range(1, 6)
    ]
    return {
        "consulta": 6,
        "nombre": "000006.png",
        "resultados": {t: vecinos for t in ("ivf", "hnsw", "exacto")},
        "evaluacion": {
            t: {"clase": "exito", "recall5": 1, "recall5_empates": 1} for t in ("ivf", "hnsw")
        },
    }


@pytest.fixture
def imagenes_de_prueba(tmp_path):
    archivos = tmp_path / "archivos"
    archivos.mkdir()
    for i in range(1, 7):
        mpimg.imsave(
            archivos / f"{i:06d}.png", np.full((32, 32), i / 6), cmap="gray", vmin=0, vmax=1
        )
    return tmp_path


def test_figura_muestra_archivos_y_valores_correctos(imagenes_de_prueba, monkeypatch):
    from matplotlib.figure import Figure

    tmp_path = imagenes_de_prueba
    archivos = tmp_path / "archivos"
    original = Figure.savefig
    capturas = []

    def guardar(self, *args, **kwargs):
        capturas.extend(
            [
                (ax.get_title(), ax.get_xlabel(), np.asarray(ax.images[0].get_array()))
                for ax in self.axes
            ]
        )
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Figure, "savefig", guardar)
    salida = figura(caso_de_prueba(), tmp_path, tmp_path)
    img = mpimg.imread(salida)
    assert img.shape[:2] == (1512, 2880)
    assert len(capturas) == 12
    for fila in (0, 6):
        assert "CONSULTA" in capturas[fila][0]
        for i in range(1, 6):
            titulo, etiqueta, pixeles = capturas[fila + i]
            assert f"{i:06d}.png" in titulo
            assert f"{i / 10:.6f}" in etiqueta
            np.testing.assert_array_equal(pixeles, mpimg.imread(archivos / f"{i:06d}.png"))


def test_archivo_inexistente_falla(tmp_path):
    with pytest.raises(FileNotFoundError):
        figura(caso_de_prueba(), tmp_path, tmp_path)
    import matplotlib.pyplot as plt

    plt.close("all")


def test_no_sobrescribe_salida(tmp_path):
    with pytest.raises(ValueError, match="existente"):
        exportar([], {}, tmp_path)


def test_rechaza_salida_dentro_del_dataset(tmp_path):
    with pytest.raises(ValueError, match="separados"):
        exportar([], {"imagenes": tmp_path}, tmp_path / "galeria")


@pytest.mark.parametrize("campo", ["consulta_sha256", "resultados_sha256"])
def test_observacion_ajena_no_publica_galeria(imagenes_de_prueba, tmp_path, campo):
    salida = tmp_path.parent / (tmp_path.name + "-salida")
    datos = [{"modalidad": "imagenes", "semilla": 1, "casos": [caso_de_prueba()]}]
    notas = {
        "imagenes/6": {
            "consulta_sha256": sha256(imagenes_de_prueba / "archivos/000006.png"),
            "resultados_sha256": huella_resultados(caso_de_prueba()),
            "texto": "No publicar",
        }
    }
    notas["imagenes/6"][campo] = "otra fuente"
    with pytest.raises(ValueError, match="no corresponde"):
        exportar(datos, {"imagenes": imagenes_de_prueba}, salida, notas)
    assert not salida.exists()


def test_galeria_publicada_y_reproduccion_real(tmp_path):
    pytest.importorskip("quipudb_native")
    raiz = Path(__file__).resolve().parents[1]
    fuentes = raiz / "benchmarks/datasets/metricas_192"
    datasets = {m: raiz / f"benchmarks/datasets/multimedia/{m}_1000" for m in ("imagenes", "audio")}
    if not fuentes.exists() or not all(p.exists() for p in datasets.values()):
        pytest.skip("fuentes locales de #190/#192 no disponibles")
    publicada = raiz / "docs/informe/galeria_similitud"
    registro = json.loads((publicada / "resultados.json").read_text())
    grupos = [preparar(datasets[m], fuentes / f"{m}_1000/COSINE") for m in datasets]
    assert grupos == registro["grupos"]
    notas = json.loads((raiz / "benchmarks/galeria_observaciones.json").read_text())
    salida = tmp_path / "galeria"
    exportar(grupos, datasets, salida, notas)
    for relativa, huella in registro["recursos"].items():
        assert sha256(publicada / relativa) == huella
        if (
            not relativa.endswith(".png")
            or registro["renderizado"]["matplotlib"] == matplotlib.__version__
        ):
            assert (salida / relativa).read_bytes() == (publicada / relativa).read_bytes()
    informe = (publicada / "README.md").read_text()
    for ruta in re.findall(r"\]\(([^)]+)\)", informe):
        assert (publicada / ruta.split("#")[0]).exists()
    for g in grupos:
        for caso in g["casos"]:
            assert caso["nombre"] in informe
            for tecnica in ("ivf", "hnsw"):
                for r in caso["resultados"][tecnica]:
                    if g["modalidad"] == "audio":
                        assert f"{r['distancia']:.6f}" in informe
                        assert sha256(publicada / "audio" / r["nombre"]) == sha256(
                            datasets["audio"] / "archivos" / r["nombre"]
                        )
