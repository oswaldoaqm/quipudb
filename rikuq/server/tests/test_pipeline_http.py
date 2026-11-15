"""Adaptador real de bytes, con operaciones de redes sustituidas, sin pesos."""

from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from app.pipeline import ModeloNoDisponible, PipelineArcFace
from app.rostros.errores import FeatureExtractionError


def _png() -> bytes:
    return cv2.imencode(".png", np.zeros((30, 40, 3), dtype=np.uint8))[1].tobytes()


def test_usa_la_misma_deteccion_alineacion_y_descripcion_del_pipeline(monkeypatch) -> None:
    pipeline = PipelineArcFace()
    pipeline._detector = object()
    pipeline._descriptor = object()
    vistos = []

    def detectar(imagen, detector):
        assert imagen.shape == (30, 40, 3)
        assert detector is pipeline._detector
        vistos.append("detectar")
        return (SimpleNamespace(caja=(1, 2, 10, 12), fiable=True),)

    def alinear(imagen, rostro):
        vistos.append("alinear")
        return np.zeros((112, 112, 3), dtype=np.uint8)

    def describir(recorte, descriptor):
        assert recorte.shape == (112, 112, 3)
        assert descriptor is pipeline._descriptor
        vistos.append("describir")
        return np.ones(512, dtype=np.float32) / np.sqrt(512)

    monkeypatch.setattr("app.rostros.deteccion.detectar", detectar)
    monkeypatch.setattr("app.rostros.deteccion.alinear", alinear)
    monkeypatch.setattr("app.rostros.embedding.describir", describir)
    imagen = pipeline.extraer(_png())
    assert vistos == ["detectar", "alinear", "describir"]
    assert (imagen.ancho, imagen.alto) == (40, 30)
    assert imagen.rostros[0].caja == (1, 2, 10, 12)
    assert len(imagen.rostros[0].vector) == 512
    assert np.linalg.norm(imagen.rostros[0].vector) == pytest.approx(1.0)


def test_un_archivo_corrupto_se_rechaza_antes_de_cargar_modelos(monkeypatch) -> None:
    def prohibido(*args):
        raise AssertionError("no descargar ni cargar modelos para un archivo invalido")

    monkeypatch.setattr("app.rostros.deteccion.crear_detector", prohibido)
    with pytest.raises(FeatureExtractionError, match="imagen valida"):
        PipelineArcFace().extraer(b"no es una imagen")


def test_un_modelo_alterado_no_se_descarga_ni_se_borra_durante_una_consulta(
    tmp_path, monkeypatch
) -> None:
    archivo = tmp_path / "modelo.onnx"
    archivo.write_bytes(b"modelo alterado de prueba")
    modelo = SimpleNamespace(ruta=lambda: archivo, sha256="hash que no coincide")
    monkeypatch.setattr("app.rostros.modelos.YUNET", modelo)
    with pytest.raises(ModeloNoDisponible, match="alterado"):
        PipelineArcFace().extraer(_png())
    assert archivo.read_bytes() == b"modelo alterado de prueba"
