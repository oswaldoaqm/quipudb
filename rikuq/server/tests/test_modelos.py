"""Identidad de los modelos ONNX: de donde salen y como se reconocen (#304)."""

from pathlib import Path

from app.rostros.modelos import MODELOS, SFACE, YUNET


def test_los_dos_modelos_del_proyecto_salen_del_zoo_de_opencv() -> None:
    for modelo in (YUNET, SFACE):
        assert modelo.url.startswith("https://github.com/opencv/opencv_zoo/")
        assert modelo.archivo.endswith(".onnx")


def test_cada_modelo_lleva_su_sha256_completo() -> None:
    """Es la unica forma de afirmar que todos corremos el mismo modelo."""

    for modelo in (YUNET, SFACE):
        assert len(modelo.sha256) == 64
        assert modelo.sha256 == modelo.sha256.lower()
        assert set(modelo.sha256) <= set("0123456789abcdef")


def test_el_detector_y_el_descriptor_son_archivos_distintos() -> None:
    assert YUNET.archivo != SFACE.archivo
    assert YUNET.sha256 != SFACE.sha256


def test_cada_modelo_resuelve_su_ruta_en_la_carpeta(tmp_path: Path) -> None:
    assert YUNET.ruta(tmp_path) == tmp_path / "face_detection_yunet_2023mar.onnx"


def test_los_modelos_viven_fuera_del_repositorio() -> None:
    """`rikuq/.gitignore` deja `datos/` afuera: 37 MB no van en la historia."""

    assert MODELOS.parent.name == "datos"
