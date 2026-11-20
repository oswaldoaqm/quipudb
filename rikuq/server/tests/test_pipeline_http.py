"""Adaptador real de bytes, con operaciones de redes sustituidas, sin pesos."""

from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from app.pipeline import ModeloNoDisponible, PipelineArcFace
from app.rostros.deteccion import DeteccionOrientada
from app.rostros.errores import FeatureExtractionError


def _png() -> bytes:
    return cv2.imencode(".png", np.zeros((30, 40, 3), dtype=np.uint8))[1].tobytes()


def _doblar_pipeline(monkeypatch, pipeline, hallazgo, vistos):
    """Sustituye las tres etapas por dobles que solo anotan que se llamaron."""

    def detectar_orientado(imagen, detector):
        assert imagen.shape == (30, 40, 3)
        assert detector is pipeline._detector
        vistos.append("detectar_orientado")
        return hallazgo

    def alinear(imagen, rostro):
        vistos.append("alinear")
        return np.zeros((112, 112, 3), dtype=np.uint8)

    def describir(recorte, descriptor):
        assert recorte.shape == (112, 112, 3)
        assert descriptor is pipeline._descriptor
        vistos.append("describir")
        return np.ones(512, dtype=np.float32) / np.sqrt(512)

    monkeypatch.setattr("app.rostros.deteccion.detectar_orientado", detectar_orientado)
    monkeypatch.setattr("app.rostros.deteccion.alinear", alinear)
    monkeypatch.setattr("app.rostros.embedding.describir", describir)


def test_usa_la_misma_deteccion_alineacion_y_descripcion_del_pipeline(monkeypatch) -> None:
    pipeline = PipelineArcFace()
    pipeline._detector = object()
    pipeline._descriptor = object()
    vistos = []
    rostro = SimpleNamespace(caja=(1, 2, 10, 12), fiable=True)
    hallazgo = DeteccionOrientada(
        imagen=np.zeros((30, 40, 3), dtype=np.uint8),
        rostros=(rostro,),
        grados=0,
        forma_original=(30, 40),
    )

    _doblar_pipeline(monkeypatch, pipeline, hallazgo, vistos)
    imagen = pipeline.extraer(_png())

    assert vistos == ["detectar_orientado", "alinear", "describir"]
    assert (imagen.ancho, imagen.alto) == (40, 30)
    assert imagen.rostros[0].caja == (1, 2, 10, 12)
    assert len(imagen.rostros[0].vector) == 512
    assert np.linalg.norm(imagen.rostros[0].vector) == pytest.approx(1.0)


def test_la_caja_que_sale_es_la_de_la_imagen_que_entro_no_la_girada(monkeypatch) -> None:
    """Con la imagen leida a 90 grados, la caja vuelve al encuadre recibido.

    Quien consulta dibuja el recuadro sobre su propia imagen. Si saliera en
    coordenadas de la version girada, el rostro se reconoceria bien y el
    recuadro apareceria en otro sitio.
    """

    pipeline = PipelineArcFace()
    pipeline._detector = object()
    pipeline._descriptor = object()
    # La imagen recibida es de 40x30; girada 90 grados mide 30x40.
    rostro = SimpleNamespace(caja=(4, 6, 8, 10), fiable=True)
    hallazgo = DeteccionOrientada(
        imagen=np.zeros((40, 30, 3), dtype=np.uint8),
        rostros=(rostro,),
        grados=90,
        forma_original=(30, 40),
    )

    _doblar_pipeline(monkeypatch, pipeline, hallazgo, [])
    imagen = pipeline.extraer(_png())

    x, y, ancho_caja, alto_caja = imagen.rostros[0].caja
    assert (x, y, ancho_caja, alto_caja) != rostro.caja
    assert 0 <= x < x + ancho_caja <= imagen.ancho
    assert 0 <= y < y + alto_caja <= imagen.alto


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
