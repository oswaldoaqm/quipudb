"""Carga real de vectores, persistencia y ausencia de extraccion e indices (#190)."""

from __future__ import annotations

import builtins
import importlib

import numpy as np
import pytest

from benchmarks.scripts.cargar_multimedia import cargar
from benchmarks.scripts.generar_multimedia import generar, precalcular
from benchmarks.scripts.multimedia_dataset import leer_precalculado


@pytest.fixture(scope="module", params=["imagenes", "audio"])
def conjunto(request, tmp_path_factory):
    pytest.importorskip("cv2")
    pytest.importorskip("soundfile")
    raiz = generar(tmp_path_factory.mktemp(request.param), request.param, 20)
    precalcular(raiz, k=16, muestra=256)
    return raiz


def test_carga_real_por_lotes_sin_extraccion_ni_indices(conjunto, tmp_path, monkeypatch):
    nativo = pytest.importorskip("quipudb_native", reason="requiere bindings actuales")
    from engine.executor import QueryProcessor
    from engine.multimedia import features

    def prohibido(*args, **kwargs):
        pytest.fail("la carga no debe extraer ni construir indices")

    for nombre in ("describe", "extract_features", "build_vocabulary"):
        monkeypatch.setattr(features, nombre, prohibido)
    original_import = builtins.__import__

    def importar(nombre, *args, **kwargs):
        if nombre in ("cv2", "soundfile"):
            pytest.fail("la carga no necesita decodificadores multimedia")
        return original_import(nombre, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", importar)
    for nombre in ("create_index", "create_ivf_index", "create_hnsw_index"):
        monkeypatch.setattr(nativo.Database, nombre, prohibido)
    original = QueryProcessor.load_media_batch
    tamanos = []

    def registrar(self, tabla, rows, **kwargs):
        tamanos.append(len(rows))
        return original(self, tabla, rows, **kwargs)

    monkeypatch.setattr(QueryProcessor, "load_media_batch", registrar)
    catalogo = tmp_path / "catalogo"
    with monkeypatch.context() as sin_scan:
        sin_scan.setattr(nativo.TableFile, "scan", prohibido)
        sin_scan.setattr(nativo.TableFile, "scan_with_rids", prohibido)
        assert cargar(conjunto, catalogo, lote=7) == 20
    assert tamanos == [7, 7, 6]
    datos, _, vectores = leer_precalculado(conjunto)
    db = nativo.Database(catalogo / "catalogo.txt")
    tabla = datos["modalidad"]
    assert not db.table_info(tabla).indexes
    registros = list(db.table(tabla).scan())
    assert len(registros) == 20
    np.testing.assert_array_equal(np.array([fila[3] for fila in registros], dtype=np.float32),
                                  vectores)
    db.close(tabla)
    with pytest.raises(ValueError, match="no esta vacio"):
        cargar(conjunto, catalogo)


def test_bindings_ausentes_falla_explicitamente(tmp_path, monkeypatch):
    original = importlib.import_module

    def importar(nombre, *args, **kwargs):
        if nombre == "quipudb_native":
            raise ImportError("no disponible")
        return original(nombre, *args, **kwargs)

    monkeypatch.setattr(importlib, "import_module", importar)
    with pytest.raises(RuntimeError, match="faltan los bindings"):
        cargar(tmp_path, tmp_path / "catalogo")
    assert not (tmp_path / "catalogo").exists()


def test_rechaza_carga_parcial(conjunto, tmp_path, monkeypatch):
    pytest.importorskip("quipudb_native")
    from engine.executor import QueryProcessor
    from engine.executor.media_load import MediaLoadReport

    def incompleto(self, tabla, rows, **kwargs):
        return MediaLoadReport(tabla, inserted=len(rows) - 1, failed=1)

    monkeypatch.setattr(QueryProcessor, "load_media_batch", incompleto)
    with pytest.raises(RuntimeError, match="carga incompleta"):
        cargar(conjunto, tmp_path / "catalogo")
