"""``ExtractFeatures`` sobre una imagen y un audio de verdad (issue #181).

Es el criterio 5, y es la unica prueba que recorre el pipeline entero:

    archivo -> SIFT o MFCC -> codebook -> histograma + TF-IDF -> VECTOR(K)

Las demas pruebas de #181 usan descriptores inventados, que es lo correcto
para comprobar las formulas. Esta usa los dos archivos de ejemplo del
repositorio, que es lo unico que comprueba que las piezas encajan.

Se saltan si falta OpenCV o soundfile; el CI instala los dos.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("cv2", reason="se necesita opencv-python-headless")
pytest.importorskip("soundfile", reason="se necesita soundfile")

from engine.multimedia.errors import FeatureExtractionError
from engine.multimedia.features import (
    EXTENSIONES_DE_AUDIO,
    EXTENSIONES_DE_IMAGEN,
    build_vocabulary,
    describe,
    extract_features,
    tiene_contenido,
)

DATOS = Path(__file__).parent / "datos"
IMAGEN = DATOS / "ejemplo.png"
AUDIO = DATOS / "ejemplo.wav"


def _variantes_de(imagen: Path, cuantas: int, destino: Path) -> list[Path]:
    """Varias copias con ruido: un dataset minimo pero no degenerado.

    Hace falta mas de un objeto porque el idf es ``log10(N / df)``: con un
    solo objeto toda palabra esta en todos y el vector entero sale nulo.
    """

    import cv2

    from engine.multimedia.sift import read_grayscale

    base = read_grayscale(imagen)
    azar = np.random.default_rng(0)
    rutas = []
    for indice in range(cuantas):
        ruido = azar.integers(-20, 21, base.shape)
        girada = np.rot90(base, indice % 4).copy()
        if girada.shape != base.shape:
            girada = base.copy()
        pixeles = np.clip(girada.astype(np.int16) + ruido, 0, 255).astype(np.uint8)
        ruta = destino / f"objeto{indice}.png"
        ok, codificado = cv2.imencode(".png", pixeles)
        assert ok
        ruta.write_bytes(codificado.tobytes())
        rutas.append(ruta)
    return rutas


# ---------------------------------------------------------------------------
# El pipeline decide por el tipo de archivo
# ---------------------------------------------------------------------------


def test_una_imagen_se_describe_con_sift() -> None:
    assert describe(IMAGEN).shape[1] == 128


def test_un_audio_se_describe_con_mfcc() -> None:
    assert describe(AUDIO).shape[1] == 13


def test_las_dos_familias_de_extension_no_se_pisan() -> None:
    assert not (EXTENSIONES_DE_IMAGEN & EXTENSIONES_DE_AUDIO)


def test_un_archivo_de_otro_tipo_da_un_error_claro(tmp_path: Path) -> None:
    ruta = tmp_path / "notas.txt"
    ruta.write_text("ni imagen ni audio", encoding="utf-8")
    with pytest.raises(FeatureExtractionError, match="no es una imagen ni un audio"):
        describe(ruta)


def test_un_archivo_sin_extension_da_un_error_claro(tmp_path: Path) -> None:
    ruta = tmp_path / "objeto"
    ruta.write_bytes(IMAGEN.read_bytes())
    with pytest.raises(FeatureExtractionError, match="no es una imagen ni un audio"):
        describe(ruta)


# ---------------------------------------------------------------------------
# De extremo a extremo: imagen
# ---------------------------------------------------------------------------


def test_una_imagen_llega_a_un_vector_de_dimension_k(tmp_path: Path) -> None:
    rutas = _variantes_de(IMAGEN, 8, tmp_path)
    vocabulario = build_vocabulary([describe(r) for r in rutas], k=16, seed=0)
    vector = extract_features(rutas[0], vocabulario)
    assert vector.shape == (16,)
    assert vector.dtype == np.float32


def test_el_vector_de_una_imagen_describe_algo(tmp_path: Path) -> None:
    rutas = _variantes_de(IMAGEN, 8, tmp_path)
    vocabulario = build_vocabulary([describe(r) for r in rutas], k=16, seed=0)
    assert tiene_contenido(extract_features(rutas[0], vocabulario))


def test_la_consulta_pasa_por_el_mismo_pipeline_que_el_dataset(tmp_path: Path) -> None:
    # Es la regla que sostiene todo: SIMILAR_TO('foto.jpg', ...) llama a esta
    # misma funcion con el mismo vocabulario. Una imagen del dataset buscada
    # como consulta tiene que caer sobre si misma.
    rutas = _variantes_de(IMAGEN, 8, tmp_path)
    vocabulario = build_vocabulary([describe(r) for r in rutas], k=16, seed=0)
    indexados = [extract_features(r, vocabulario) for r in rutas]
    consulta = extract_features(rutas[3], vocabulario)
    parecidos = [float(consulta @ v) for v in indexados]
    assert int(np.argmax(parecidos)) == 3


def test_dos_extracciones_de_la_misma_imagen_dan_el_mismo_vector(tmp_path: Path) -> None:
    rutas = _variantes_de(IMAGEN, 8, tmp_path)
    vocabulario = build_vocabulary([describe(r) for r in rutas], k=16, seed=0)
    assert np.array_equal(
        extract_features(rutas[0], vocabulario), extract_features(rutas[0], vocabulario)
    )


# ---------------------------------------------------------------------------
# De extremo a extremo: audio
# ---------------------------------------------------------------------------


def test_un_audio_llega_a_un_vector_de_dimension_k() -> None:
    # El audio de ejemplo son dos tonos: cada ventana es un objeto distinto
    # para el vocabulario, asi que alcanza para que el idf no se anule.
    ventanas = describe(AUDIO)
    dataset = [ventanas[i::6] for i in range(6)]
    vocabulario = build_vocabulary(dataset, k=8, seed=0)
    vector = extract_features(AUDIO, vocabulario)
    assert vector.shape == (8,)
    assert vector.dtype == np.float32


def test_el_vector_de_un_audio_describe_algo() -> None:
    ventanas = describe(AUDIO)
    vocabulario = build_vocabulary([ventanas[i::6] for i in range(6)], k=8, seed=0)
    assert tiene_contenido(extract_features(AUDIO, vocabulario))


def test_la_imagen_y_el_audio_no_comparten_vocabulario() -> None:
    # 128 valores por descriptor contra 13: mezclarlos no es una mala idea,
    # es imposible, y el error tiene que decirlo al cuantizar.
    vocabulario = build_vocabulary([describe(IMAGEN)[i::4] for i in range(4)], k=4, seed=0)
    from engine.multimedia.kmeans import CodebookError

    with pytest.raises(CodebookError, match="dimension"):
        vocabulario.histogram(describe(AUDIO))


# ---------------------------------------------------------------------------
# Objetos de los que no sale nada
# ---------------------------------------------------------------------------


def _png_liso(destino: Path) -> Path:
    import cv2

    ok, codificado = cv2.imencode(".png", np.full((64, 64), 128, dtype=np.uint8))
    assert ok
    destino.write_bytes(codificado.tobytes())
    return destino


def test_una_imagen_sin_puntos_da_el_vector_nulo(tmp_path: Path) -> None:
    # Criterio 4: un vector definido, no una division por cero y no un error.
    rutas = _variantes_de(IMAGEN, 8, tmp_path)
    vocabulario = build_vocabulary([describe(r) for r in rutas], k=16, seed=0)
    vector = extract_features(_png_liso(tmp_path / "liso.png"), vocabulario)
    assert vector.shape == (16,)
    assert not np.any(vector)


def test_el_vector_nulo_se_reconoce_como_sin_contenido(tmp_path: Path) -> None:
    # Bajo coseno el vector nulo queda a distancia 1 de todo, incluido de si
    # mismo, asi que esos objetos no son alcanzables por una busqueda.
    # `tiene_contenido` es lo que permite no indexarlos.
    rutas = _variantes_de(IMAGEN, 8, tmp_path)
    vocabulario = build_vocabulary([describe(r) for r in rutas], k=16, seed=0)
    assert not tiene_contenido(extract_features(_png_liso(tmp_path / "liso.png"), vocabulario))


def test_un_audio_demasiado_corto_da_el_vector_nulo(tmp_path: Path) -> None:
    import soundfile as sf

    ruta = tmp_path / "corto.wav"
    sf.write(ruta, np.zeros(160, dtype=np.float32), 16_000)
    vocabulario = build_vocabulary([describe(AUDIO)[i::6] for i in range(6)], k=8, seed=0)
    assert not np.any(extract_features(ruta, vocabulario))


def test_un_archivo_roto_sigue_siendo_un_error(tmp_path: Path) -> None:
    # La diferencia con el caso anterior: un .png a medio descargar no es una
    # foto en blanco, y no debe entrar al dataset como si lo fuera.
    rutas = _variantes_de(IMAGEN, 8, tmp_path)
    vocabulario = build_vocabulary([describe(r) for r in rutas], k=16, seed=0)
    roto = tmp_path / "roto.png"
    roto.write_bytes(IMAGEN.read_bytes()[:60] + b"\x00" * 200)
    with pytest.raises(FeatureExtractionError, match="no es una imagen"):
        extract_features(roto, vocabulario)


def test_una_ruta_que_no_existe_sigue_siendo_un_error(tmp_path: Path) -> None:
    rutas = _variantes_de(IMAGEN, 8, tmp_path)
    vocabulario = build_vocabulary([describe(r) for r in rutas], k=16, seed=0)
    with pytest.raises(FeatureExtractionError, match="no se puede leer"):
        extract_features(tmp_path / "fantasma.png", vocabulario)
