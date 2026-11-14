"""El vector de 128 numeros que describe un rostro (#305).

Las pruebas que miran caras de verdad usan LFW, que no se versiona: se saltan
solas si el dataset no esta bajado (#301).
"""

from pathlib import Path

import numpy as np
import pytest

from app.rostros.embedding import (
    DIMENSIONES,
    describir,
)
from app.rostros.errores import EmptyObjectError
from app.rostros.modelos import SFACE, YUNET

LFW = Path(__file__).resolve().parents[2] / "datos" / "lfw"

sin_modelos = pytest.mark.skipif(
    not (YUNET.ruta().is_file() and SFACE.ruta().is_file()),
    reason="faltan los modelos ONNX: corre `python -m app.rostros.modelos`",
)
sin_dataset = pytest.mark.skipif(not LFW.is_dir(), reason="LFW no esta bajado (#301)")


class _DescriptorFalso:
    """Devuelve un vector fijo, para probar lo que hace `describir` con el."""

    def __init__(self, vector: np.ndarray) -> None:
        self._vector = vector

    def feature(self, _recorte: np.ndarray) -> np.ndarray:
        return self._vector.reshape(1, -1)


def _recorte() -> np.ndarray:
    return np.zeros((112, 112, 3), dtype=np.uint8)


def test_el_vector_tiene_128_numeros() -> None:
    crudo = np.full(DIMENSIONES, 3.0, dtype=np.float32)

    vector = describir(_recorte(), _DescriptorFalso(crudo))

    assert vector.shape == (DIMENSIONES,)
    assert vector.dtype == np.float32


def test_un_vector_de_otro_largo_se_rechaza_antes_de_comparar() -> None:
    """Si cambia el modelo, se entera aqui y no al definir la columna de #302."""

    crudo = np.ones(64, dtype=np.float32)

    with pytest.raises(ValueError, match="64 numeros"):
        describir(_recorte(), _DescriptorFalso(crudo))


def test_un_vector_nulo_no_describe_nada() -> None:
    crudo = np.zeros(DIMENSIONES, dtype=np.float32)

    with pytest.raises(EmptyObjectError, match="nulo"):
        describir(_recorte(), _DescriptorFalso(crudo))
