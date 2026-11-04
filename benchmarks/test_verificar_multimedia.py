"""Auditoria independiente y reproduccion desde carpetas sin artefactos previos."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import numpy as np
import pytest

from benchmarks.scripts.cargar_multimedia import cargar
from benchmarks.scripts.generar_multimedia import generar, precalcular
from benchmarks.scripts.multimedia_dataset import sha256
from benchmarks.scripts.verificar_multimedia import contar_distintos, verificar

RAIZ = Path(__file__).resolve().parents[1]
SCRIPT = RAIZ / "benchmarks/scripts/ejecutar_multimedia.py"


def test_cuenta_repeticiones_exactas_en_disco(tmp_path):
    manifiesto = tmp_path / "objetos.csv"
    manifiesto.write_text("id,nombre,ruta,sha256\n1,a,a,uno\n2,b,b,dos\n3,c,c,dos\n")
    vectores = np.asarray([[1, 0], [0, 1], [1, 0]], dtype=np.float32)
    assert contar_distintos(vectores, manifiesto) == dict(
        vectores_distintos=2, grupos_repetidos=1, max_repeticiones=2,
        archivos_distintos_sha256=2,
    )


@pytest.fixture(scope="module")
def paquete(tmp_path_factory):
    pytest.importorskip("quipudb_native")
    pytest.importorskip("soundfile")
    raiz = tmp_path_factory.mktemp("auditoria")
    datos = generar(raiz, "audio", 20)
    precalcular(datos, k=16, muestra=256)
    catalogo = raiz / "catalogo"
    cargar(datos, catalogo)
    return datos, catalogo


def test_verifica_sin_materializar_ni_extraer_si_consultas_cero(paquete, monkeypatch):
    import quipudb_native as nativo
    from engine.multimedia import features

    def prohibido(*args, **kwargs):
        pytest.fail("no debe materializar ni extraer en este modo")

    monkeypatch.setattr(nativo.TableFile, "scan", prohibido)
    monkeypatch.setattr(nativo.TableFile, "scan_with_rids", prohibido)
    monkeypatch.setattr(features, "extract_features", prohibido)
    datos, catalogo = paquete
    resultado = verificar(datos, catalogo, consultas=0)
    assert resultado["registros_verificados"] == resultado["archivos_distintos_sha256"] == 20
    assert resultado["forma"] == [20, 16]
    assert resultado["indices"] == resultado["vectores_nulos"] == 0
    assert resultado["extracciones_directas_verificadas"] == 0
    matriz = np.load(datos / "caracteristicas/vectores.npy")
    assert resultado["vectores_distintos"] == len(np.unique(matriz, axis=0))


def test_rechaza_persistencia_alterada(paquete, tmp_path):
    import quipudb_native as nativo

    datos, original = paquete
    catalogo = tmp_path / "alterado"
    shutil.copytree(original, catalogo)
    db = nativo.Database(catalogo / "catalogo.txt")
    tabla = db.table("audio")
    registro = tabla.search(1)[0]
    registro[1] = "alterado.wav"
    tabla.update(1, registro)
    db.flush()
    del tabla
    db.close("audio")
    with pytest.raises(ValueError, match="persistencia incorrecta"):
        verificar(datos, catalogo, consultas=0)


def test_verificar_no_crea_catalogo_inexistente(tmp_path):
    catalogo = tmp_path / "inexistente"
    with pytest.raises(ValueError, match="catalogo no existe"):
        verificar(tmp_path, catalogo)
    assert not catalogo.exists()


def entorno_nativo():
    nativo = pytest.importorskip("quipudb_native")
    return dict(os.environ, PYTHONPATH=str(Path(nativo.__file__).parent))


def test_reproduce_desde_copia_sin_datasets_ni_ejecutar_ignorado(tmp_path):
    env = entorno_nativo()
    pytest.importorskip("cv2")
    pytest.importorskip("soundfile")
    copia = tmp_path / "copia"
    ignorar = shutil.ignore_patterns("__pycache__", ".pytest_cache")
    shutil.copytree(RAIZ / "engine", copia / "engine", ignore=ignorar)
    shutil.copytree(RAIZ / "benchmarks/scripts", copia / "benchmarks/scripts", ignore=ignorar)
    anterior = tmp_path / "anterior"
    anterior.mkdir()
    (anterior / "conservar.txt").write_text("no modificar\n")
    salida = copia / "benchmarks/datasets/multimedia"
    reportes = salida / "reportes"
    script = copia / "benchmarks/scripts/ejecutar_multimedia.py"
    comando = [sys.executable, "-B", str(script), "--modalidad", "ambas", "--tamanos", "20",
               "--salida", str(salida), "--reportes", str(reportes), "--k", "16",
               "--muestra", "256", "--lote", "7", "--consultas", "3",
               "--preservar", str(anterior)]
    resultado = subprocess.run(comando, cwd=tmp_path, env=env, capture_output=True, text=True)
    assert resultado.returncode == 0, resultado.stdout + resultado.stderr
    for modalidad in ("imagenes", "audio"):
        for etapa in ("generar", "precalcular", "cargar", "verificar"):
            medida = json.loads((reportes / f"{modalidad}_20_{etapa}.json").read_text())
            assert medida["segundos"] > 0 and medida["rss_max_bytes"] > 0
        carga = json.loads((reportes / f"{modalidad}_20_cargar.json").read_text())
        assert carga["extraccion_e_indices_bloqueados"]
        assert (carga["filas"], carga["lotes"]) == (20, 3)
        verificacion = json.loads((reportes / f"{modalidad}_20_verificar.json").read_text())
        assert verificacion["registros_verificados"] == 20
        assert verificacion["extracciones_directas_verificadas"] == 3
    assert json.loads((reportes / "final.json").read_text())["archivos_anteriores_inalterados"] == 1
    huella = sha256(salida / "audio_20/caracteristicas/vectores.npy")
    repetido = subprocess.run(comando, cwd=tmp_path, env=env, capture_output=True, text=True)
    assert repetido.returncode != 0 and "ya existe" in repetido.stderr
    assert sha256(salida / "audio_20/caracteristicas/vectores.npy") == huella
    # La CLI independiente tampoco necesita el ejecutor ni archivos locales ignorados.
    verificador = copia / "benchmarks/scripts/verificar_multimedia.py"
    ver = subprocess.run([sys.executable, "-B", str(verificador), str(salida / "audio_20"),
                          "--catalogo", str(salida / "catalogos/audio_20"), "--consultas", "0"],
                         cwd=tmp_path, env=env, capture_output=True, text=True)
    assert ver.returncode == 0, ver.stderr
    assert json.loads(ver.stdout)["registros_verificados"] == 20


def test_limite_de_memoria_detiene_la_secuencia(tmp_path):
    env = entorno_nativo()
    pytest.importorskip("soundfile")
    salida = tmp_path / "datos"
    reportes = tmp_path / "reportes"
    resultado = subprocess.run(
        [sys.executable, "-B", str(SCRIPT), "--modalidad", "audio", "--tamanos", "20",
         "--salida", str(salida), "--reportes", str(reportes), "--max-rss-mib", "1"],
        cwd=tmp_path, env=env, capture_output=True, text=True,
    )
    assert resultado.returncode != 0 and "RSS" in resultado.stdout
    assert not (salida / "audio_20").exists()
    assert not (reportes / "audio_20_precalcular.log").exists()
    assert not (reportes / "final.json").exists()


@pytest.mark.parametrize("opciones", [("--k", "1"), ("--tamanos", "0"),
                                     ("--min-libre-mib", "999999999")])
def test_rechaza_parametros_antes_de_generar(tmp_path, opciones):
    resultado = subprocess.run(
        [sys.executable, "-B", str(SCRIPT), "--modalidad", "audio",
         "--salida", str(tmp_path / "datos"), "--reportes", str(tmp_path / "reportes"),
         *opciones], env=entorno_nativo(), capture_output=True, text=True,
    )
    assert resultado.returncode != 0
    assert not (tmp_path / "datos").exists()
