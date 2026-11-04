"""Recall con empates y recorrido nativo completo de #191."""

import csv
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchmarks.scripts.ejecutar_benchmarks_vectoriales import evaluar, limpiar
from benchmarks.scripts.generar_multimedia import generar, precalcular


def test_empates_no_ocultan_vecinos_estrictos():
    ref = [(i, 0 if i < 3 else 1) for i in range(10)]
    otros = [(i, 1) for i in range(20, 30)]
    assert evaluar(otros, ref) == (0, 0.7)
    assert evaluar(ref, ref) == (1, 1)
    with pytest.raises(ValueError):
        evaluar([(0, 0)] * 10, ref)


def test_excluye_solo_el_propio():
    class Tabla:
        def read(self, rid):
            return [rid.slot]

    matches = [SimpleNamespace(rid=SimpleNamespace(page=1, slot=i), distance=0) for i in range(11)]
    assert len(limpiar(matches, Tabla(), 5)) == 10
    assert (1, 5) not in [r for r, _ in limpiar(matches, Tabla(), 5)]


@pytest.mark.parametrize("modalidad", ["imagenes", "audio"])
def test_recorrido_real(tmp_path, modalidad):
    native = pytest.importorskip("quipudb_native")
    pytest.importorskip("cv2")
    pytest.importorskip("soundfile")
    generar(tmp_path / "entrada", modalidad, 30, 20260906)
    dataset = tmp_path / "entrada" / f"{modalidad}_30"
    precalcular(dataset, k=16, muestra=256)
    salida = tmp_path / "salida"
    script = Path(__file__).parent / "scripts/ejecutar_benchmarks_vectoriales.py"
    env = os.environ | {"PYTHONPATH": str(Path(native.__file__).parent)}
    cmd = [
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
    ]
    run = subprocess.run(cmd, env=env, capture_output=True, text=True, check=False)
    assert run.returncode == 0, (
        run.stderr + "\n" + "\n".join(p.read_text() for p in salida.glob("*.log"))
    )
    assert (salida / "completo.json").exists()
    entrada = json.loads((salida / "entrada.json").read_text())
    for tecnica in ["ivf", "hnsw"]:
        with (salida / f"{tecnica}_consultas.csv").open() as f:
            filas = list(csv.DictReader(f))
        assert {int(r["consulta_id"]) for r in filas} == set(entrada["ids"])
        for r in filas:
            assert 0 <= float(r["recall10"]) <= float(r["recall_empates"]) <= 1
            if tecnica == "hnsw" and int(r["parametro"]) >= 30:
                assert float(r["recall10"]) == 1
    repetido = subprocess.run(cmd, env=env, capture_output=True, check=False)
    assert repetido.returncode != 0
    segunda = tmp_path / "segunda"
    cmd[cmd.index(str(salida))] = str(segunda)
    subprocess.run(cmd, env=env, capture_output=True, check=True)
    assert json.loads((segunda / "entrada.json").read_text())["ids"] == entrada["ids"]
    assert (segunda / "exactos.json").read_bytes() == (salida / "exactos.json").read_bytes()
    for tecnica in ("ivf", "hnsw"):
        with (salida / f"{tecnica}_consultas.csv").open() as f:
            primera = list(csv.DictReader(f))
        with (segunda / f"{tecnica}_consultas.csv").open() as f:
            otra = list(csv.DictReader(f))
        for filas in (primera, otra):
            for fila in filas:
                fila.pop("tiempo_ns")
        assert primera == otra


def test_resumen_no_confunde_mediana_con_promedio(tmp_path):
    from benchmarks.scripts.resumir_vectoriales import resumir

    assert resumir(tmp_path) == []
    carpeta = tmp_path / "audio_20"
    carpeta.mkdir()
    (carpeta / "completo.json").write_text("{}")
    for tecnica in ("exacto", "ivf", "hnsw"):
        (carpeta / f"{tecnica}_memoria.json").write_text('{"rss_max_bytes": 100}')
        with (carpeta / f"{tecnica}_consultas.csv").open("w") as f:
            f.write("parametro,repeticion,tiempo_ns,recall10,recall_empates\n")
            f.write("1,0,1000000,0.5,1\n1,0,9000000,0.5,1\n1,1,3000000,0.5,1\n")
    assert all(f["consulta_ms"] == 4 for f in resumir(tmp_path))


def test_casi_empate_no_cuenta_como_empate():
    ref = [(i, 1.0) for i in range(10)]
    assert evaluar([(20, 1.0 + 1e-12)], ref) == (0, 0)


def test_duplicados_nativos_y_self_fuera_del_top11(tmp_path):
    native = pytest.importorskip("quipudb_native")
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
    for i in range(30):
        tabla.insert([i, [1.0, 0.0]])
    metric = native.VectorMetric.COSINE
    ivf = db.create_ivf_index("t", "i", "v", [[1.0, 0.0]])
    hnsw = db.create_hnsw_index(
        "t", "h", "v", metric, native.HnswParams(m=4, ef_construction=32, seed=20260906)
    )
    for propio in (0, 29):
        exactos = limpiar(
            native.scan_k_nearest_vector(tabla, "v", [1.0, 0.0], 11, metric), tabla, propio
        )
        for indice, parametro in ((ivf, 1), (hnsw, 30)):
            obtenido = limpiar(indice.k_nearest([1.0, 0.0], 11, metric, parametro), tabla, propio)
            assert obtenido == exactos
            assert evaluar(obtenido, exactos) == (1, 1)
    db.close("t")
