"""Cache de los modelos ONNX y su SHA-256 (#304), sin red."""

import hashlib
from pathlib import Path
from typing import Self

import httpx
import pytest

from app.rostros.modelos import ARCFACE, YUNET, Modelo, ModeloError, main, traer

CONTENIDO = b"onnx de mentira"
HUELLA = hashlib.sha256(CONTENIDO).hexdigest()


def _modelo(sha: str = HUELLA) -> Modelo:
    return Modelo(archivo="falso.onnx", url="https://example.invalid/falso.onnx", sha256=sha)


class _Respuesta:
    """Lo minimo de httpx.stream que usa `traer`."""

    def __init__(self, contenido: bytes) -> None:
        self._contenido = contenido

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def raise_for_status(self) -> None:
        return None

    def iter_bytes(self, _tamano: int) -> list[bytes]:
        return [self._contenido]


def _servir(monkeypatch: pytest.MonkeyPatch, contenido: bytes) -> list[str]:
    """Reemplaza la descarga y devuelve la lista de URLs que se pidieron."""

    pedidas: list[str] = []

    def stream(_metodo: str, url: str, **_kwargs: object) -> _Respuesta:
        pedidas.append(url)
        return _Respuesta(contenido)

    monkeypatch.setattr(httpx, "stream", stream)
    return pedidas


def test_traer_baja_el_modelo_la_primera_vez(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pedidas = _servir(monkeypatch, CONTENIDO)

    ruta = traer(_modelo(), tmp_path)

    assert ruta == tmp_path / "falso.onnx"
    assert ruta.read_bytes() == CONTENIDO
    assert len(pedidas) == 1


def test_traer_no_toca_la_red_si_ya_esta(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "falso.onnx").write_bytes(CONTENIDO)
    pedidas = _servir(monkeypatch, CONTENIDO)

    traer(_modelo(), tmp_path)

    assert pedidas == []


def test_traer_rebaja_un_archivo_que_no_coincide(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Una descarga vieja o cortada se reemplaza en vez de darse por buena."""

    (tmp_path / "falso.onnx").write_bytes(b"descarga a medias")
    pedidas = _servir(monkeypatch, CONTENIDO)

    ruta = traer(_modelo(), tmp_path)

    assert len(pedidas) == 1
    assert ruta.read_bytes() == CONTENIDO


def test_traer_rechaza_un_modelo_distinto_del_medido(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Otro modelo da otros vectores, y los umbrales dejarian de significar nada."""

    _servir(monkeypatch, b"otro modelo cualquiera")

    with pytest.raises(ModeloError, match="no es el modelo esperado"):
        traer(_modelo(), tmp_path)

    assert list(tmp_path.iterdir()) == []


def test_traer_no_deja_un_archivo_a_medias(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def cae(*_args: object, **_kwargs: object) -> None:
        raise httpx.ConnectError("sin red")

    monkeypatch.setattr(httpx, "stream", cae)

    with pytest.raises(ModeloError, match="no se pudo bajar"):
        traer(_modelo(), tmp_path)

    assert list(tmp_path.iterdir()) == []


def test_el_detector_sale_del_zoo_de_opencv() -> None:
    assert YUNET.url.startswith("https://github.com/opencv/opencv_zoo/")


def test_el_descriptor_es_arcface_como_pide_el_enunciado() -> None:
    """ "extrae embeddings faciales con modelos preentrenados (FaceNet, ArcFace)"."""

    assert "arcface" in ARCFACE.archivo.lower()
    assert "buffalo" in ARCFACE.url.lower()


def test_los_dos_modelos_son_onnx() -> None:
    for modelo in (YUNET, ARCFACE):
        assert modelo.archivo.endswith(".onnx")
        assert len(modelo.sha256) == 64


def test_cada_modelo_resuelve_su_ruta_en_la_carpeta(tmp_path: Path) -> None:
    assert YUNET.ruta(tmp_path) == tmp_path / "face_detection_yunet_2023mar.onnx"


def test_main_trae_los_dos_modelos(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    """Un solo comando deja listo el pipeline entero, sin correr nada mas."""

    traidos: list[str] = []

    def traer_falso(modelo, carpeta, log=None):
        traidos.append(modelo.archivo)
        destino = carpeta / modelo.archivo
        carpeta.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(CONTENIDO)
        return destino

    monkeypatch.setattr("app.rostros.modelos.traer", traer_falso)

    assert main(["--carpeta", str(tmp_path)]) == 0
    assert traidos == [YUNET.archivo, ARCFACE.archivo]
    assert "bytes" in capsys.readouterr().out


def test_main_explica_el_error_en_vez_de_reventar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    def cae(*_args: object, **_kwargs: object) -> None:
        raise ModeloError("el zoo no responde")

    monkeypatch.setattr("app.rostros.modelos.traer", cae)

    assert main(["--carpeta", str(tmp_path)]) == 1
    assert "error: el zoo no responde" in capsys.readouterr().err
