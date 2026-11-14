"""El vector de 128 numeros que describe un rostro (#305).

Las pruebas que miran caras de verdad usan LFW, que no se versiona: se saltan
solas si el dataset no esta bajado (#301).
"""

from pathlib import Path

import numpy as np
import pytest

from app.rostros.deteccion import crear_alineador, crear_detector
from app.rostros.embedding import (
    DIMENSIONES,
    crear_descriptor,
    describir,
    distancia,
    vectores_de,
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


def test_el_vector_sale_con_norma_1() -> None:
    """SFace devuelve normas de 4 a 5; QuipuDB no normaliza al consultar."""

    crudo = np.full(DIMENSIONES, 3.0, dtype=np.float32)

    vector = describir(_recorte(), _DescriptorFalso(crudo))

    assert vector.shape == (DIMENSIONES,)
    assert float(np.linalg.norm(vector)) == pytest.approx(1.0, abs=1e-6)


def test_normalizar_no_cambia_la_direccion() -> None:
    crudo = np.arange(DIMENSIONES, dtype=np.float32) + 1.0

    vector = describir(_recorte(), _DescriptorFalso(crudo))

    assert np.allclose(vector, crudo / np.linalg.norm(crudo), atol=1e-6)


def test_un_vector_de_otro_largo_se_rechaza_antes_de_comparar() -> None:
    """Si cambia el modelo, se entera aqui y no al definir la columna de #302."""

    crudo = np.ones(64, dtype=np.float32)

    with pytest.raises(ValueError, match="64 numeros"):
        describir(_recorte(), _DescriptorFalso(crudo))


def test_un_vector_nulo_no_describe_nada() -> None:
    crudo = np.zeros(DIMENSIONES, dtype=np.float32)

    with pytest.raises(EmptyObjectError, match="nulo"):
        describir(_recorte(), _DescriptorFalso(crudo))


def test_la_distancia_de_un_vector_consigo_mismo_es_cero() -> None:
    uno = np.zeros(DIMENSIONES, dtype=np.float32)
    uno[0] = 1.0

    assert distancia(uno, uno) == pytest.approx(0.0, abs=1e-6)


def test_la_distancia_crece_cuando_los_vectores_se_separan() -> None:
    """Menor es mas parecido, como `vector_distance` del motor."""

    uno = np.zeros(DIMENSIONES, dtype=np.float32)
    uno[0] = 1.0
    perpendicular = np.zeros(DIMENSIONES, dtype=np.float32)
    perpendicular[1] = 1.0

    assert distancia(uno, perpendicular) == pytest.approx(1.0, abs=1e-6)
    assert distancia(uno, -uno) == pytest.approx(2.0, abs=1e-6)


def test_la_dimension_medida_es_128() -> None:
    assert DIMENSIONES == 128


# --- con los modelos y las caras de verdad ------------------------------------


@sin_modelos
def test_el_descriptor_y_el_alineador_son_el_mismo_sface() -> None:
    """SFace hace las dos cosas; crear dos cargaria 37 MB dos veces."""

    assert type(crear_descriptor()) is type(crear_alineador())


@sin_modelos
@sin_dataset
def test_sface_devuelve_de_verdad_128_numeros() -> None:
    foto = min(LFW.glob("*/*.jpg"))

    vectores = vectores_de(foto, crear_detector(), crear_descriptor())

    assert len(vectores) == 1
    assert vectores[0].shape == (DIMENSIONES,)
    assert vectores[0].dtype == np.float32
    assert float(np.linalg.norm(vectores[0])) == pytest.approx(1.0, abs=1e-5)


@sin_modelos
@sin_dataset
def test_la_misma_foto_da_siempre_el_mismo_vector() -> None:
    foto = min(LFW.glob("*/*.jpg"))
    detector, descriptor = crear_detector(), crear_descriptor()

    primera = vectores_de(foto, detector, descriptor)[0]
    segunda = vectores_de(foto, detector, descriptor)[0]

    assert np.array_equal(primera, segunda)


def _tres_identidades(minimo: int = 2) -> list[Path]:
    """Carpetas de LFW con al menos `minimo` fotos, las tres primeras."""

    elegidas = []
    for carpeta in sorted(p for p in LFW.iterdir() if p.is_dir()):
        if len(list(carpeta.glob("*.jpg"))) >= minimo:
            elegidas.append(carpeta)
        if len(elegidas) == 3:
            break
    return elegidas


@sin_modelos
@sin_dataset
def test_la_misma_persona_queda_mas_cerca_que_otra() -> None:
    """Es la prueba que decide si todo el pipeline sirve para algo."""

    carpetas = _tres_identidades()
    assert len(carpetas) == 3, "LFW no trajo tres identidades con dos fotos"

    detector, descriptor = crear_detector(), crear_descriptor()
    por_persona = {}
    for carpeta in carpetas:
        fotos = sorted(carpeta.glob("*.jpg"))[:2]
        por_persona[carpeta.name] = [vectores_de(foto, detector, descriptor)[0] for foto in fotos]

    for nombre, (una, otra) in por_persona.items():
        propia = distancia(una, otra)
        ajenas = [
            distancia(una, ajeno)
            for otro, vectores in por_persona.items()
            if otro != nombre
            for ajeno in vectores
        ]
        assert propia < min(ajenas), f"{nombre}: {propia:.3f} no es menor que {min(ajenas):.3f}"
