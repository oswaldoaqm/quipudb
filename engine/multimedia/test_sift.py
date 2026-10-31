"""Forma de los descriptores SIFT que devuelve la extraccion (issue #176).

No necesitan el core: el modulo es puro sobre archivos. Si OpenCV no esta
instalado las pruebas se saltan, para que un companero que todavia no corrio
``pip install -r requirements.txt`` no vea el resto de la suite en rojo. El
CI no puede saltarselas: instala OpenCV y comprueba que importa antes de
llamar a pytest.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("cv2", reason="se necesita opencv-python-headless")

# El import va despues del importorskip a proposito: engine.multimedia.sift
# importa cv2 al cargarse, asi que arriba del todo reventaria el modulo antes
# de que pytest pudiera saltarse las pruebas.
from engine.multimedia.sift import DESCRIPTOR_LENGTH, extract_sift

EJEMPLO = Path(__file__).parent / "datos" / "ejemplo.png"


def test_la_imagen_de_ejemplo_tiene_puntos_de_interes() -> None:
    assert len(extract_sift(EJEMPLO)) > 0


def test_cada_descriptor_tiene_128_valores() -> None:
    descriptores = extract_sift(EJEMPLO)
    assert descriptores.ndim == 2
    assert descriptores.shape[1] == DESCRIPTOR_LENGTH


def test_los_descriptores_vienen_en_float32() -> None:
    # El K-Means de #178 promedia estos valores: con enteros, cada centroide
    # se truncaria en cada iteracion.
    assert extract_sift(EJEMPLO).dtype == np.float32


def test_la_extraccion_es_reproducible() -> None:
    # Misma imagen, mismos descriptores: si SIFT no fuera determinista, el
    # codebook cambiaria en cada construccion del dataset.
    assert np.array_equal(extract_sift(EJEMPLO), extract_sift(EJEMPLO))
