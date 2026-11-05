import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from benchmarks.scripts.comparar_metricas import coincidencia
from benchmarks.scripts.generar_multimedia import generar, precalcular
from benchmarks.scripts.multimedia_dataset import validar_vectores


def test_normalizacion_y_empates():
    a = np.asarray([[1, 0], [0, 1], [1, 0]], dtype=np.float32)
    validar_vectores(a, 3, 2)
    np.testing.assert_allclose(np.sum((a - a[0]) ** 2, axis=1), 2 - 2 * (a @ a[0]))
    assert coincidencia([[1, 0], [1, 1]], [[1, 1], [1, 2]]) == 1
    with pytest.raises(ValueError):
        validar_vectores(a * 2, 3, 2)


def test_ambas_metricas(tmp_path):
    native = pytest.importorskip("quipudb_native")
    generar(tmp_path / "datos", "audio", 30, 20260906)
    dataset = tmp_path / "datos/audio_30"
    precalcular(dataset, k=16, muestra=256)
    salida = tmp_path / "resultados"
    script = Path(__file__).parent / "scripts/comparar_metricas.py"
    subprocess.run(
        [
            sys.executable,
            "-B",
            str(script),
            str(dataset),
            "--salida",
            str(salida),
            "--consultas",
            "20",
            "--repeticiones",
            "1",
        ],
        check=True,
        env=os.environ | {"PYTHONPATH": str(Path(native.__file__).parent)},
    )
    assert (salida / "comparacion.csv").exists()
    for m in ("COSINE", "EUCLIDEAN"):
        assert json.loads((salida / m / "entorno.json").read_text())["metrica"] == m


@pytest.mark.parametrize("metrica", ["COSINE", "EUCLIDEAN"])
def test_empates_exactos_nativos(tmp_path, metrica):
    native = pytest.importorskip("quipudb_native")
    from benchmarks.scripts.ejecutar_benchmarks_vectoriales import evaluar, limpiar

    db = native.Database(tmp_path / "catalogo.txt")
    schema = native.Schema(
        table_name="t",
        columns=[
            native.Column("id", native.DataType.INT),
            native.Column("v", native.DataType.VECTOR, 2),
        ],
        key_column=0,
    )
    tabla = db.create_table(schema, native.kind.HEAP)
    for i in range(20):
        tabla.insert([i, [1.0, 0.0]])
    matches = native.scan_k_nearest_vector(
        tabla, "v", [1.0, 0.0], 11, getattr(native.VectorMetric, metrica)
    )
    vecinos = limpiar(matches, tabla, 0)
    assert len(vecinos) == 10
    assert all(d == 0 for _, d in vecinos)
    assert evaluar(vecinos, vecinos) == (1, 1)
    db.close("t")
