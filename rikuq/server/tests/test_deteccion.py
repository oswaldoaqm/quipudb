"""Deteccion de rostros (#304).

Las pruebas que no necesitan una cara usan un detector de mentira: asi
comprueban como se interpreta y se ordena lo que YuNet devuelve sin depender
de ninguna foto.
"""

from pathlib import Path

import numpy as np
import pytest

from app.rostros.deteccion import UMBRAL_CONFIANZA, crear_detector, detectar
from app.rostros.errores import EmptyObjectError
from app.rostros.modelos import MODELOS, YUNET

sin_modelos = pytest.mark.skipif(
    not YUNET.ruta().is_file(),
    reason="falta el modelo de deteccion",
)


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


def test_el_umbral_por_omision_es_estricto() -> None:
    """Con el umbral bajo, YuNet marca manchas y cada falso rostro es una identidad."""

    assert UMBRAL_CONFIANZA == 0.9


@sin_modelos
def test_el_detector_se_crea_con_el_umbral_que_se_le_pide() -> None:
    assert crear_detector(umbral=0.5) is not None


def test_los_modelos_viven_fuera_del_repositorio() -> None:
    assert MODELOS.parent.name == "datos"
