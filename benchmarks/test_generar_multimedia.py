"""Archivos reales, reproducibilidad y equivalencia con ExtractFeatures (#190)."""

from __future__ import annotations

import subprocess
import sys
import wave
from pathlib import Path

import numpy as np
import pytest

from benchmarks.scripts.generar_multimedia import generar, precalcular
from benchmarks.scripts.multimedia_dataset import (
    filas,
    leer_generacion,
    leer_precalculado,
    escribir_json,
    sha256,
    validar_vectores,
)
from engine.multimedia.features import Vocabulary, extract_features


@pytest.fixture(scope="module", params=["imagenes", "audio"])
def conjunto(request, tmp_path_factory):
    pytest.importorskip("cv2")
    pytest.importorskip("soundfile")
    raiz = generar(tmp_path_factory.mktemp(request.param), request.param, 30)
    precalcular(raiz, k=16, muestra=256)
    return raiz


def test_archivos_y_vectores_coinciden_con_el_pipeline(conjunto):
    datos, metadata, vectores = leer_precalculado(conjunto)
    vocabulario = Vocabulary.load(conjunto / "caracteristicas" / "vocabulario.npz")
    huellas = set()
    for identificador, _, ruta in filas(conjunto, datos):
        huellas.add(sha256(ruta))
        if datos["modalidad"] == "imagenes":
            import cv2

            assert cv2.imread(str(ruta), cv2.IMREAD_GRAYSCALE).shape == (128, 128)
        else:
            with wave.open(str(ruta)) as wav:
                assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate(),
                        wav.getnframes()) == (1, 2, 16000, 8000)
        esperado = extract_features(ruta, vocabulario, **metadata["extraccion"])
        np.testing.assert_array_equal(vectores[identificador - 1], esperado)
    assert len(huellas) == datos["cantidad"]
    assert len(np.unique(vectores, axis=0)) == datos["cantidad"]
    assert vocabulario.documents == 30


def test_reproducibilidad_completa_y_generacion_independiente(conjunto, tmp_path):
    datos = leer_generacion(conjunto)
    copia = generar(tmp_path / "copia", datos["modalidad"], 30)
    precalcular(copia, k=16, muestra=256)
    for archivo in conjunto.rglob("*"):
        if archivo.is_file():
            assert archivo.read_bytes() == (copia / archivo.relative_to(conjunto)).read_bytes()
    # Ni el orden de llamadas ni N cambian el contenido de un objeto con el mismo id.
    corto = generar(tmp_path / "corto", datos["modalidad"], 20)
    distinta = generar(tmp_path / "distinta", datos["modalidad"], 20, semilla=1)
    for _, nombre, ruta in filas(corto, leer_generacion(corto)):
        assert ruta.read_bytes() == (conjunto / "archivos" / nombre).read_bytes()
        assert ruta.read_bytes() != (distinta / "archivos" / nombre).read_bytes()


def test_no_sobrescribe_etapas_completadas(conjunto):
    datos = leer_generacion(conjunto)
    with pytest.raises(ValueError, match="ya existe"):
        generar(conjunto.parent, datos["modalidad"], 30)
    with pytest.raises(ValueError, match="ya existe"):
        precalcular(conjunto)


@pytest.mark.parametrize("dano", ["archivo", "manifiesto", "vectores", "vocabulario"])
def test_integridad_del_paquete(conjunto, tmp_path, dano):
    import shutil

    copia = tmp_path / "copia"
    shutil.copytree(conjunto, copia)
    objetivo = {
        "archivo": next((copia / "archivos").iterdir()),
        "manifiesto": copia / "objetos.csv",
        "vectores": copia / "caracteristicas" / "vectores.npy",
        "vocabulario": copia / "caracteristicas" / "vocabulario.npz",
    }[dano]
    objetivo.write_bytes(objetivo.read_bytes() + b"alterado")
    with pytest.raises(ValueError, match="SHA-256"):
        leer_precalculado(copia)


@pytest.mark.parametrize("defecto", ["nan", "inf", "nulo", "norma", "forma", "dtype"])
def test_rechaza_vectores_invalidos(defecto):
    matriz = np.eye(3, dtype=np.float32)
    if defecto == "forma":
        matriz = matriz[:, :2]
    elif defecto == "dtype":
        matriz = matriz.astype(np.float64)
    else:
        matriz[0] = {"nan": np.nan, "inf": np.inf, "nulo": 0, "norma": 2}[defecto]
    with pytest.raises(ValueError):
        validar_vectores(matriz, 3, 3)


def test_cli_desde_otro_directorio_y_etapas_separadas(tmp_path):
    pytest.importorskip("soundfile")
    script = Path(__file__).parent / "scripts" / "generar_multimedia.py"
    subprocess.run([sys.executable, "-B", str(script), "generar", "--modalidad", "audio",
                    "--tamanos", "20", "--semilla", "190", "--salida", str(tmp_path)],
                   cwd=tmp_path, check=True, capture_output=True, text=True)
    raiz = tmp_path / "audio_20"
    assert not (raiz / "caracteristicas").exists()
    assert leer_generacion(raiz)["semilla"] == 190
    subprocess.run([sys.executable, "-B", str(script), "precalcular", str(raiz),
                    "--k", "16", "--muestra", "256"], cwd=tmp_path, check=True,
                   capture_output=True, text=True)
    assert leer_precalculado(raiz)[2].shape == (20, 16)


def test_error_de_extraccion_no_publica_vectores(tmp_path, monkeypatch):
    from engine.multimedia import features

    raiz = generar(tmp_path, "audio", 20)

    def fallar(*args, **kwargs):
        raise ValueError("fallo de extraccion")

    monkeypatch.setattr(features, "describe", fallar)
    with pytest.raises(ValueError, match="fallo de extraccion"):
        precalcular(raiz)
    assert not (raiz / "caracteristicas").exists()


def test_idf_nulo_no_publica_ni_deja_temporales(tmp_path):
    pytest.importorskip("soundfile")
    raiz = generar(tmp_path, "audio", 1)
    with pytest.raises(ValueError, match="vectores nulos"):
        precalcular(raiz, k=4, muestra=32)
    assert not (raiz / "caracteristicas").exists()
    assert not list(raiz.glob(".precalculo-*"))


@pytest.mark.parametrize("defecto", ["cantidad", "id", "ruta"])
def test_valida_manifiesto_ademas_del_hash(tmp_path, defecto):
    raiz = generar(tmp_path, "audio", 20)
    datos = leer_generacion(raiz)
    manifiesto = raiz / "objetos.csv"
    contenido = manifiesto.read_text()
    if defecto == "cantidad":
        datos["cantidad"] = 21
    elif defecto == "id":
        contenido = contenido.replace("1,000001.wav", "2,000001.wav", 1)
    else:
        contenido = contenido.replace("archivos/000001.wav", "../fuera.wav", 1)
    manifiesto.write_text(contenido)
    datos["manifiesto_sha256"] = sha256(manifiesto)
    escribir_json(raiz / "generacion.json", datos)
    with pytest.raises(ValueError):
        list(filas(raiz, leer_generacion(raiz)))
