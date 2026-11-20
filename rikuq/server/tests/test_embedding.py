"""El vector de 512 numeros que describe un rostro (#305).

Las pruebas que miran caras de verdad usan LFW, que no se versiona: se saltan
solas si el dataset no esta bajado (#301).
"""

from pathlib import Path

import numpy as np
import pytest

from app.rostros.deteccion import crear_detector
from app.rostros.embedding import (
    DIMENSIONES,
    crear_descriptor,
    describir,
    distancia,
    main,
    vectores_de,
)
from app.rostros.errores import EmptyObjectError
from app.rostros.modelos import ARCFACE, YUNET

LFW = Path(__file__).resolve().parents[2] / "datos" / "lfw"

sin_modelos = pytest.mark.skipif(
    not (YUNET.ruta().is_file() and ARCFACE.ruta().is_file()),
    reason="faltan los modelos ONNX: corre `python -m app.rostros.modelos`",
)
sin_dataset = pytest.mark.skipif(not LFW.is_dir(), reason="LFW no esta bajado (#301)")


class _DescriptorFalso:
    """Imita lo que `describir` usa de `cv2.dnn.Net`: setInput y forward."""

    def __init__(self, vector: np.ndarray) -> None:
        self._vector = vector
        self.entrada = None

    def setInput(self, blob: np.ndarray) -> None:
        self.entrada = blob

    def forward(self) -> np.ndarray:
        return self._vector.reshape(1, -1)


def _recorte() -> np.ndarray:
    return np.zeros((112, 112, 3), dtype=np.uint8)


def test_el_vector_sale_con_norma_1() -> None:
    """ArcFace devuelve normas cerca de 10; QuipuDB no normaliza al consultar."""

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


def test_la_dimension_medida_es_512() -> None:
    """ArcFace devuelve 512; es lo que fija VECTOR(512) en #302."""

    assert DIMENSIONES == 512


# --- con los modelos y las caras de verdad ------------------------------------


@sin_modelos
@sin_dataset
def test_arcface_devuelve_de_verdad_512_numeros() -> None:
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


@sin_modelos
@sin_dataset
def test_main_imprime_el_vector_y_la_distancia(capsys) -> None:
    fotos = sorted(_tres_identidades()[0].glob("*.jpg"))[:2]

    assert main([str(f) for f in fotos]) == 0

    salida = capsys.readouterr().out
    assert "norma=1.0000" in salida
    assert "distancia" in salida


@sin_modelos
def test_main_anota_la_imagen_rota_y_sigue(tmp_path: Path, capsys) -> None:
    rota = tmp_path / "rota.jpg"
    rota.write_bytes(b"esto no es una imagen")

    assert main([str(rota)]) == 1
    assert "no es una imagen" in capsys.readouterr().err


@sin_modelos
@sin_dataset
def test_el_vector_del_sujeto_sale_del_rostro_centrado() -> None:
    """Para cargar un dataset hace falta un vector por archivo, el del sujeto.

    Se monta una foto ancha con la misma cara dos veces, una centrada y otra
    pegada al borde, y se comprueba que el vector coincide con el de la
    centrada. `vectores_de` sigue devolviendolos todos, que es lo que quiere
    quien consulta una foto cualquiera.
    """

    import cv2

    from app.rostros.deteccion import alinear, detectar_orientado
    from app.rostros.embedding import describir, distancia, vector_del_sujeto
    from app.rostros.lectura import leer_color

    cara = leer_color(next(iter(sorted(LFW.glob("*/*.jpg")))))
    alto, ancho = cara.shape[:2]
    lienzo = np.zeros((alto, ancho * 3, 3), dtype=np.uint8)
    lienzo[:, :ancho] = cara  # al borde
    lienzo[:, ancho : ancho * 2] = cara  # centrada
    archivo = Path(__file__).with_name("_sujeto_tmp.png")
    cv2.imwrite(str(archivo), lienzo)
    try:
        detector, descriptor = crear_detector(), crear_descriptor()
        hallazgo = detectar_orientado(lienzo, detector)
        assert len(hallazgo.rostros) >= 2, "la foto de prueba tiene que traer dos caras"

        centrada = describir(alinear(hallazgo.imagen, hallazgo.sujeto), descriptor)
        assert distancia(vector_del_sujeto(archivo, detector, descriptor), centrada) < 1e-6
    finally:
        archivo.unlink(missing_ok=True)
