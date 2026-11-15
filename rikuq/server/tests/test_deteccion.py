"""Deteccion y alineado de rostros (#304).

Las pruebas que no necesitan una cara usan un detector de mentira: asi
comprueban como se interpreta y se ordena lo que YuNet devuelve sin depender
de ninguna foto. La que si necesita una cara se salta cuando el dataset no
esta bajado, porque LFW no se versiona (#301).
"""

from pathlib import Path

import numpy as np
import pytest

from app.rostros.deteccion import (
    LADO_ALINEADO,
    UMBRAL_CONFIANZA,
    RostroDetectado,
    alinear,
    crear_detector,
    detectar,
    rostros_de,
)
from app.rostros.errores import EmptyObjectError
from app.rostros.modelos import MODELOS, YUNET

LFW = Path(__file__).resolve().parents[2] / "datos" / "lfw"

sin_modelos = pytest.mark.skipif(
    not YUNET.ruta().is_file(),
    reason="faltan los modelos ONNX: corre `python -m app.rostros.modelos`",
)
sin_dataset = pytest.mark.skipif(not LFW.is_dir(), reason="LFW no esta bajado (#301)")


def _fila(x=10, y=20, w=30, h=40, confianza=0.99) -> np.ndarray:
    """Una fila de YuNet: caja, cinco landmarks y la confianza al final."""

    return np.array(
        [x, y, w, h, 15, 25, 35, 25, 25, 35, 18, 48, 32, 48, confianza],
        dtype=np.float32,
    )


class _DetectorFalso:
    def __init__(self, *filas: np.ndarray) -> None:
        self._filas = filas
        self.tamano: tuple[int, int] | None = None

    def setInputSize(self, tamano: tuple[int, int]) -> None:
        self.tamano = tamano

    def detect(self, _imagen: np.ndarray) -> tuple[int, np.ndarray | None]:
        if not self._filas:
            return 1, None
        return 1, np.vstack(self._filas)


def _imagen(alto: int = 100, ancho: int = 80) -> np.ndarray:
    return np.zeros((alto, ancho, 3), dtype=np.uint8)


def test_detectar_desarma_la_caja_los_puntos_y_la_confianza() -> None:
    rostros = detectar(_imagen(), _DetectorFalso(_fila()))

    assert len(rostros) == 1
    assert rostros[0].caja == (10, 20, 30, 40)
    assert rostros[0].confianza == pytest.approx(0.99)
    assert rostros[0].landmarks == ((15, 25), (35, 25), (25, 35), (18, 48), (32, 48))


def test_detectar_le_pasa_el_tamano_de_la_imagen_al_detector() -> None:
    """YuNet necesita (ancho, alto), que es al reves que el shape de numpy."""

    detector = _DetectorFalso(_fila())

    detectar(_imagen(alto=100, ancho=80), detector)

    assert detector.tamano == (80, 100)


def test_detectar_devuelve_todos_los_rostros_el_mas_confiable_primero() -> None:
    detector = _DetectorFalso(_fila(confianza=0.91), _fila(confianza=0.99), _fila(confianza=0.95))

    rostros = detectar(_imagen(), detector)

    assert [round(r.confianza, 2) for r in rostros] == [0.99, 0.95, 0.91]


def test_una_imagen_sin_rostros_no_es_un_archivo_roto(tmp_path: Path) -> None:
    """Un paisaje es una imagen valida y vacia: la carga lo anota y sigue."""

    with pytest.raises(EmptyObjectError) as error:
        detectar(_imagen(), _DetectorFalso(), ruta=tmp_path / "paisaje.jpg")

    assert "paisaje.jpg" in str(error.value)
    assert error.value.path == tmp_path / "paisaje.jpg"


def test_el_umbral_por_omision_deja_pasar_rostros_pequenos() -> None:
    """Entre dos extremos observados, y por eso 0.7.

    - Con 0.9 se perdian rostros de verdad: una cara de 50 px puntua 0.90 y
      una de 35 px, 0.785.
    - Con 0.6 aparecia un rostro inexistente en el fondo de una foto de dos
      personas, con 0.617.
    """

    assert UMBRAL_CONFIANZA == 0.7
    assert UMBRAL_CONFIANZA > 0.617
    assert UMBRAL_CONFIANZA < 0.785


def test_un_rostro_chico_se_detecta_pero_se_marca_como_poco_fiable() -> None:
    """El descriptor se degrada con la resolucion aunque la deteccion acierte."""

    chico = RostroDetectado((0, 0, 40, 40), 0.8, ((0, 0),) * 5, _fila())
    grande = RostroDetectado((0, 0, 200, 200), 0.8, ((0, 0),) * 5, _fila())

    assert chico.lado == 40
    assert not chico.fiable
    assert grande.fiable


def test_el_lado_es_el_menor_de_la_caja() -> None:
    """Una caja alargada no tiene mas cara que su lado corto."""

    assert RostroDetectado((0, 0, 200, 30), 0.8, ((0, 0),) * 5, _fila()).lado == 30


@sin_modelos
def test_alinear_devuelve_un_recorte_del_tamano_que_espera_arcface() -> None:
    imagen = np.random.default_rng(0).integers(0, 256, (200, 200, 3), dtype=np.uint8)
    rostro = RostroDetectado((50, 50, 60, 60), 0.99, ((0, 0),) * 5, _fila(50, 50, 60, 60))

    recorte = alinear(imagen, rostro)

    assert recorte.shape == (LADO_ALINEADO, LADO_ALINEADO, 3)
    assert recorte.dtype == np.uint8


@sin_modelos
def test_el_detector_se_crea_con_el_umbral_que_se_le_pide() -> None:
    assert crear_detector(umbral=0.5) is not None


@sin_modelos
@sin_dataset
def test_una_foto_de_verdad_da_un_rostro_alineado() -> None:
    """La unica prueba que mira una cara real; se salta si no esta LFW."""

    fotos = sorted(LFW.glob("*/*.jpg"))
    assert fotos, "LFW esta pero vacio"

    recortes = rostros_de(fotos[0], crear_detector())

    assert len(recortes) >= 1
    assert recortes[0].shape == (LADO_ALINEADO, LADO_ALINEADO, 3)


@sin_modelos
@sin_dataset
def test_un_paisaje_de_verdad_lanza_el_error_de_imagen_vacia(tmp_path: Path) -> None:
    import cv2

    paisaje = tmp_path / "paisaje.png"
    degradado = np.tile(np.arange(200, dtype=np.uint8), (200, 1))
    paisaje.write_bytes(cv2.imencode(".png", cv2.cvtColor(degradado, cv2.COLOR_GRAY2BGR))[1])

    with pytest.raises(EmptyObjectError):
        rostros_de(paisaje, crear_detector())


def test_los_modelos_viven_fuera_del_repositorio() -> None:
    assert MODELOS.parent.name == "datos"


@sin_modelos
def test_main_anota_la_imagen_sin_rostros_y_sigue_con_las_demas(tmp_path, capsys) -> None:
    """Igual que hara la carga del dataset en #302: no corta al primer problema."""

    import cv2

    from app.rostros.deteccion import main

    plana = tmp_path / "plana.png"
    plana.write_bytes(cv2.imencode(".png", np.zeros((120, 120, 3), dtype=np.uint8))[1])
    rota = tmp_path / "rota.jpg"
    rota.write_bytes(b"esto no es una imagen")

    codigo = main([str(plana), str(rota)])

    assert codigo == 1
    salida = capsys.readouterr()
    assert "no hay ningun rostro en plana.png" in salida.err
    assert "no es una imagen" in salida.err
