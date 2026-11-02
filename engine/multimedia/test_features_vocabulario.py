"""El vocabulario: codebook mas IDF, y su viaje a disco (issue #181).

El criterio 3 pide que el IDF se calcule sobre el dataset y se guarde junto
al codebook. Van juntos porque el codebook dice QUE palabras hay y el IDF
CUANTO pesa cada una: aplicar uno con el IDF de otro dataset produce
histogramas que parecen correctos y no lo son.

No necesitan OpenCV ni soundfile: se trabaja sobre matrices de descriptores
inventadas, con grupos cuyos centros se conocen.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from engine.multimedia.features import (
    FORMATO,
    Vocabulary,
    VocabularyError,
    build_vocabulary,
)
from engine.multimedia.kmeans import huella_de
from engine.multimedia.weights import WeightError

# Tres zonas bien separadas del espacio: seran tres palabras distintas.
ZONAS = np.array([[0.0, 0.0], [10.0, 10.0], [0.0, 10.0]], dtype=np.float32)


def _objeto(zonas: list[int], por_zona: int = 12, semilla: int = 0) -> np.ndarray:
    """Descriptores de un objeto que solo usa las zonas indicadas."""

    azar = np.random.default_rng(semilla)
    nubes = [ZONAS[z] + 0.2 * azar.standard_normal((por_zona, 2)) for z in zonas]
    return np.concatenate(nubes).astype(np.float32)


def _dataset() -> list[np.ndarray]:
    """Cuatro objetos: la zona 0 esta en todos, la zona 2 en uno solo."""

    return [
        _objeto([0, 1], semilla=1),
        _objeto([0, 1], semilla=2),
        _objeto([0], semilla=3),
        _objeto([0, 2], semilla=4),
    ]


# ---------------------------------------------------------------------------
# Construccion
# ---------------------------------------------------------------------------


def test_el_vocabulario_tiene_k_palabras_y_un_idf_por_palabra() -> None:
    vocabulario = build_vocabulary(_dataset(), k=3, seed=0)
    assert vocabulario.k == 3
    assert vocabulario.idf.shape == (3,)


def test_el_vocabulario_recuerda_de_cuantos_objetos_salio() -> None:
    assert build_vocabulary(_dataset(), k=3, seed=0).documents == 4


def test_la_dimension_es_la_de_los_descriptores() -> None:
    assert build_vocabulary(_dataset(), k=3, seed=0).dim == 2


def test_la_palabra_que_esta_en_todos_los_objetos_no_pesa() -> None:
    # La zona 0 aparece en los cuatro objetos: df = N, idf = 0.
    vocabulario = build_vocabulary(_dataset(), k=3, seed=0)
    palabra_comun = int(vocabulario.codebook.assign(ZONAS[0][None, :])[0])
    assert vocabulario.idf[palabra_comun] == 0.0


def test_la_palabra_rara_pesa_mas_que_la_frecuente() -> None:
    # La zona 2 esta en un solo objeto y la zona 1 en dos.
    vocabulario = build_vocabulary(_dataset(), k=3, seed=0)
    rara = int(vocabulario.codebook.assign(ZONAS[2][None, :])[0])
    frecuente = int(vocabulario.codebook.assign(ZONAS[1][None, :])[0])
    assert vocabulario.idf[rara] > vocabulario.idf[frecuente] > 0.0


def test_el_df_se_cuenta_por_objeto_y_no_por_descriptor() -> None:
    # Un objeto con cincuenta descriptores en la misma palabra la cuenta una
    # vez, igual que en texto un termino repetido no sube el df.
    pocos = build_vocabulary([_objeto([0], 5), _objeto([1], 5)], k=2, seed=0)
    muchos = build_vocabulary([_objeto([0], 60), _objeto([1], 5)], k=2, seed=0)
    assert sorted(pocos.idf.tolist()) == sorted(muchos.idf.tolist())


def test_el_vocabulario_es_reproducible() -> None:
    uno = build_vocabulary(_dataset(), k=3, seed=7)
    otro = build_vocabulary(_dataset(), k=3, seed=7)
    assert uno.identifier == otro.identifier
    assert np.array_equal(uno.idf, otro.idf)


# ---------------------------------------------------------------------------
# Lo que no se puede construir
# ---------------------------------------------------------------------------


def test_un_dataset_vacio_se_rechaza() -> None:
    with pytest.raises(VocabularyError, match="hacen falta objetos"):
        build_vocabulary([], k=3)


def test_un_dataset_sin_ningun_descriptor_se_rechaza() -> None:
    vacio = np.empty((0, 2), dtype=np.float32)
    with pytest.raises(VocabularyError, match="ninguno de los objetos"):
        build_vocabulary([vacio, vacio], k=3)


def test_los_objetos_sin_descriptores_no_cuentan_para_el_idf() -> None:
    # Si contaran, subirian N sin subir ningun df y falsearian todos los pesos.
    vacio = np.empty((0, 2), dtype=np.float32)
    assert build_vocabulary([*_dataset(), vacio], k=3, seed=0).documents == 4


def test_pedir_mas_palabras_que_descriptores_se_rechaza() -> None:
    with pytest.raises(VocabularyError):
        build_vocabulary([_objeto([0], 2)], k=50)


# ---------------------------------------------------------------------------
# Ida y vuelta a disco
# ---------------------------------------------------------------------------


def test_el_vocabulario_sobrevive_a_guardarlo_y_releerlo(tmp_path: Path) -> None:
    original = build_vocabulary(_dataset(), k=3, seed=5)
    original.save(tmp_path / "vocabulario.npz")
    leido = Vocabulary.load(tmp_path / "vocabulario.npz")
    assert np.array_equal(leido.codebook.centroids, original.codebook.centroids)
    assert np.array_equal(leido.idf, original.idf)
    assert leido.documents == original.documents


def test_el_vocabulario_releido_produce_el_mismo_histograma(tmp_path: Path) -> None:
    # Es lo que de verdad importa: que la consulta leida de disco caiga en
    # las mismas palabras y con los mismos pesos que el dataset.
    original = build_vocabulary(_dataset(), k=3, seed=5)
    original.save(tmp_path / "vocabulario.npz")
    leido = Vocabulary.load(tmp_path / "vocabulario.npz")
    consulta = _objeto([0, 2], semilla=99)
    assert np.array_equal(leido.histogram(consulta), original.histogram(consulta))


def test_guardar_crea_las_carpetas_que_falten(tmp_path: Path) -> None:
    destino = tmp_path / "indices" / "multimedia" / "voc.npz"
    build_vocabulary(_dataset(), k=3, seed=0).save(destino)
    assert destino.is_file()


# ---------------------------------------------------------------------------
# Deteccion de un vocabulario que no corresponde
# ---------------------------------------------------------------------------


def test_un_vocabulario_alterado_se_rechaza(tmp_path: Path) -> None:
    ruta = tmp_path / "voc.npz"
    original = build_vocabulary(_dataset(), k=3, seed=0)
    original.save(ruta)
    with ruta.open("wb") as salida:
        np.savez(
            salida,
            formato=np.asarray(FORMATO),
            centroids=original.codebook.centroids + 1.0,
            seed=np.asarray(0),
            identifier=np.asarray(original.identifier),
            idf=original.idf,
            documents=np.asarray(original.documents),
        )
    with pytest.raises(VocabularyError, match="no coincide con su huella"):
        Vocabulary.load(ruta)


def test_un_idf_de_otro_tamano_que_el_codebook_se_rechaza(tmp_path: Path) -> None:
    # Es el sintoma de un archivo armado a mano o de dos vocabularios
    # mezclados; sin esta comprobacion el histograma saldria con la forma
    # equivocada recien al multiplicar.
    ruta = tmp_path / "voc.npz"
    original = build_vocabulary(_dataset(), k=3, seed=0)
    with ruta.open("wb") as salida:
        np.savez(
            salida,
            formato=np.asarray(FORMATO),
            centroids=original.codebook.centroids,
            seed=np.asarray(0),
            identifier=np.asarray(huella_de(original.codebook.centroids)),
            idf=np.zeros(5),
            documents=np.asarray(4),
        )
    with pytest.raises(VocabularyError, match="pesos de idf"):
        Vocabulary.load(ruta)


def test_un_vocabulario_de_otro_formato_se_rechaza(tmp_path: Path) -> None:
    ruta = tmp_path / "voc.npz"
    original = build_vocabulary(_dataset(), k=3, seed=0)
    with ruta.open("wb") as salida:
        np.savez(
            salida,
            formato=np.asarray(FORMATO + 1),
            centroids=original.codebook.centroids,
            seed=np.asarray(0),
            identifier=np.asarray(original.identifier),
            idf=original.idf,
            documents=np.asarray(4),
        )
    with pytest.raises(VocabularyError, match="formato"):
        Vocabulary.load(ruta)


def test_un_vocabulario_que_no_existe_da_un_error_claro(tmp_path: Path) -> None:
    with pytest.raises(VocabularyError, match="no se puede leer"):
        Vocabulary.load(tmp_path / "fantasma.npz")


def test_dos_datasets_distintos_dan_huellas_distintas() -> None:
    # Es lo que permite detectar que una consulta se cuantizo con otro
    # vocabulario, que es el error silencioso del pipeline.
    uno = build_vocabulary(_dataset(), k=3, seed=1)
    otro = build_vocabulary([_objeto([0], 40, 9), _objeto([1], 40, 8)], k=3, seed=1)
    assert uno.identifier != otro.identifier


# ---------------------------------------------------------------------------
# Comparar vocabularios y casos limite del histograma
# ---------------------------------------------------------------------------


def test_dos_vocabularios_iguales_comparan_iguales() -> None:
    # Mismo defecto que en Codebook: el == generado comparaba el idf, que es
    # un ndarray, y reventaba con ValueError en vez de dar un booleano.
    dataset = _dataset()
    assert build_vocabulary(dataset, k=3, seed=6) == build_vocabulary(dataset, k=3, seed=6)


def test_el_vocabulario_releido_es_igual_al_original(tmp_path: Path) -> None:
    original = build_vocabulary(_dataset(), k=3, seed=6)
    original.save(tmp_path / "voc.npz")
    assert Vocabulary.load(tmp_path / "voc.npz") == original


def test_un_vocabulario_no_es_igual_a_otra_cosa() -> None:
    assert build_vocabulary(_dataset(), k=3, seed=0) != "un vocabulario"


def test_un_objeto_sin_descriptores_da_el_vector_nulo() -> None:
    # Mismo contrato que extract_features: el criterio 4 vale tambien al
    # llamar al histograma directamente.
    vocabulario = build_vocabulary(_dataset(), k=3, seed=0)
    vacio = np.empty((0, 2), dtype=np.float32)
    assert vocabulario.histogram(vacio).tolist() == [0.0, 0.0, 0.0]


def test_la_variante_se_comprueba_antes_de_cuantizar() -> None:
    # Asignar un millon de descriptores para despues rechazar el nombre de
    # la variante seria tirar todo ese trabajo.
    vocabulario = build_vocabulary(_dataset(), k=3, seed=0)
    with pytest.raises(WeightError, match="variante de tf"):
        vocabulario.histogram(_objeto([0]), variant="sublineal")
