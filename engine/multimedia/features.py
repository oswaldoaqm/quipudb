"""``ExtractFeatures(objeto)``: de un archivo multimedia al vector que se indexa.

Es el ultimo paso de la seccion 2.4.1 y el que cierra el pipeline:

    imagen -> SIFT (#176)  ─┐
                            ├─> codebook (#178) -> histograma + TF-IDF -> VECTOR(K)
    audio  -> MFCC (#177)  ─┘                           (este modulo)

SIFT y MFCC describen un objeto con una matriz de N x D, y N cambia de un
objeto a otro: dos matrices de distinto tamano no se pueden restar. El
codebook las lleva a un espacio comun reemplazando cada descriptor por la
palabra mas cercana, y aqui se cuenta cuantas veces aparece cada palabra.
El resultado es un vector de dimension K, siempre la misma, que entra en una
columna ``VECTOR(K)`` (#180).

Por que TF-IDF y no un conteo pelado
------------------------------------

Una palabra visual que aparece en todas las imagenes no distingue ninguna,
igual que la palabra "de" no distingue ningun documento. El IDF la apaga. Se
usan **las mismas formulas que la busqueda de texto de la Parte 3**
(``core/include/quipudb/index/text_weights.hpp``), que son las de la
diapositiva:

    idf(w)   = log10(N / df_w)              0 si la palabra no esta en ninguno
    tf_w     = 1 + log10(tf)                variante "log", la de la lamina
             = tf                           variante "raw"
    peso     = tf_w(tf) * idf(w)

No es una eleccion estetica: si el histograma visual pesara distinto que el
textual, los numeros del informe no se podrian comparar entre las partes 3
y 4.

El vector sale normalizado a norma 1. Con vectores normalizados el producto
punto vale lo mismo que el coseno (ver la cabecera de ``vector_metric.hpp``),
asi que el indice se ahorra calcular dos normas en cada comparacion.

La regla que lo sostiene todo
-----------------------------

**La consulta tiene que pasar por el mismo vocabulario que el dataset.** Es
la funcion que llama ``SIMILAR_TO('foto_consulta.jpg', ...)``: si el codebook
o el IDF fueran otros, la palabra 7 significaria otra cosa, el histograma
saldria igual de bien formado y la busqueda devolveria resultados sin sentido
sin avisar. Por eso el IDF se guarda junto al codebook y el ``Vocabulary``
viaja entero, con la huella que permite comprobarlo.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from engine.multimedia.errors import EmptyObjectError, FeatureExtractionError
from engine.multimedia.kmeans import Codebook, CodebookError, build_codebook, huella_de
from engine.multimedia.weights import (
    TF_LOG,
    TF_RAW,
    inverse_document_frequency,
    tf_weight,
)

FORMATO = 1
"""Version del formato en disco del vocabulario."""

EXTENSIONES_DE_IMAGEN = frozenset({".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"})
EXTENSIONES_DE_AUDIO = frozenset({".wav", ".flac", ".ogg", ".mp3", ".aiff", ".aif"})
"""Con que pipeline se describe cada archivo. Se decide por la extension y no
abriendo el archivo a ver que pasa: probar los dos extractores con cada objeto
costaria el doble en el caso malo, y un error de OpenCV sobre un .wav no
diria que el problema es el pipeline elegido."""


class VocabularyError(ValueError):
    """El vocabulario no se puede construir, leer o aplicar."""


@dataclass(frozen=True, slots=True, eq=False)
class Vocabulary:
    """El codebook con el IDF que se midio sobre el mismo dataset.

    Los dos van juntos a proposito. El codebook dice que palabras hay y el
    IDF cuanto pesa cada una; aplicar uno con el IDF de otro dataset da
    histogramas que parecen correctos y no lo son.
    """

    codebook: Codebook
    idf: np.ndarray
    documents: int

    def __eq__(self, other: object) -> bool:
        """Mismo codebook, mismo idf y mismo dataset detras. Ver ``Codebook``."""

        if not isinstance(other, Vocabulary):
            return NotImplemented
        return (
            self.documents == other.documents
            and self.codebook == other.codebook
            and np.array_equal(self.idf, other.idf)
        )

    @property
    def k(self) -> int:
        return self.codebook.k

    @property
    def dim(self) -> int:
        return self.codebook.dim

    @property
    def identifier(self) -> str:
        """La huella del codebook: la que tiene que coincidir con la del dataset."""

        return self.codebook.identifier

    def histogram(self, descriptors: np.ndarray, *, variant: str = TF_LOG) -> np.ndarray:
        """Cuenta las palabras de un objeto y las pesa con TF-IDF.

        Devuelve un vector de dimension K normalizado a norma 1. Un objeto
        cuyas palabras tengan todas IDF cero -- porque aparecen en todos los
        objetos del dataset -- da el vector nulo: no hay nada que lo
        distinga, y eso es informacion, no un fallo. Un objeto sin ningun
        descriptor tambien, por el mismo criterio 4 que aplica
        ``extract_features``.
        """

        # La variante se comprueba ANTES de cuantizar: asignar un millon de
        # descriptores para despues rechazar el nombre de la variante seria
        # tirar todo ese trabajo.
        tf_weight(np.zeros(1), variant)

        descriptores = np.asarray(descriptors)
        if descriptores.size == 0:
            # Mismo contrato que `extract_features`: un objeto sin nada que
            # describir da el vector nulo, no un error.
            return np.zeros(self.k, dtype=np.float32)

        palabras = self.codebook.assign(descriptores)
        conteos = np.bincount(palabras, minlength=self.k).astype(np.float64)
        pesos = tf_weight(conteos, variant) * self.idf
        return _normalizar(pesos)

    def save(self, path: str | Path) -> None:
        """Guarda el vocabulario entero: centroides, IDF y de cuantos objetos salio."""

        ruta = Path(path)
        ruta.parent.mkdir(parents=True, exist_ok=True)
        with ruta.open("wb") as salida:
            np.savez(
                salida,
                formato=np.asarray(FORMATO),
                centroids=self.codebook.centroids,
                seed=np.asarray(self.codebook.seed),
                identifier=np.asarray(self.codebook.identifier),
                idf=self.idf,
                documents=np.asarray(self.documents),
            )

    @classmethod
    def load(cls, path: str | Path) -> Vocabulary:
        """Lee un vocabulario y comprueba que el IDF corresponda a su codebook."""

        ruta = Path(path)
        try:
            with np.load(ruta, allow_pickle=False) as datos:
                formato = int(datos["formato"])
                centroides = np.ascontiguousarray(datos["centroids"], dtype=np.float32)
                semilla = int(datos["seed"])
                huella = str(datos["identifier"])
                idf = np.ascontiguousarray(datos["idf"], dtype=np.float64)
                documentos = int(datos["documents"])
        except (OSError, ValueError, KeyError) as error:
            raise VocabularyError(
                f"no se puede leer el vocabulario {ruta.name!r}: {error}"
            ) from None

        if formato != FORMATO:
            raise VocabularyError(
                f"el vocabulario {ruta.name!r} es del formato {formato} y este motor lee "
                f"el {FORMATO}"
            )
        esperada = huella_de(centroides)
        if esperada != huella:
            raise VocabularyError(
                f"el vocabulario {ruta.name!r} no coincide con su huella: "
                f"dice {huella!r} y sus centroides dan {esperada!r}"
            )
        if idf.shape != (len(centroides),):
            raise VocabularyError(
                f"el vocabulario {ruta.name!r} tiene {len(centroides)} palabras y "
                f"{idf.size} pesos de idf"
            )
        return cls(Codebook(centroides, semilla, huella), idf, documentos)


def build_vocabulary(
    descriptors_por_objeto: Sequence[np.ndarray],
    k: int,
    *,
    seed: int = 0,
    max_descriptors: int | None = None,
) -> Vocabulary:
    """Construye el codebook y mide el IDF sobre el mismo conjunto de objetos.

    Se hace en dos pasadas y no en una: primero todos los descriptores juntos
    deciden donde caen las K palabras, y recien con las palabras ya fijas se
    puede contar en cuantos OBJETOS aparece cada una. El df se cuenta por
    objeto y no por descriptor, igual que en texto se cuenta en cuantos
    documentos esta un termino y no cuantas veces en total.
    """

    if not descriptors_por_objeto:
        raise VocabularyError("hacen falta objetos para construir un vocabulario")

    matrices = [np.asarray(d, dtype=np.float32) for d in descriptors_por_objeto]
    con_descriptores = [m for m in matrices if m.size > 0]
    if not con_descriptores:
        raise VocabularyError("ninguno de los objetos tiene descriptores")

    try:
        codebook = build_codebook(
            np.concatenate(con_descriptores),
            k,
            seed=seed,
            max_descriptors=max_descriptors,
        )
    except CodebookError as error:
        raise VocabularyError(str(error)) from None

    # df por objeto: una palabra cuenta una vez por objeto en el que aparece,
    # aunque le toquen cincuenta descriptores de ese mismo objeto.
    df = np.zeros(k, dtype=np.int64)
    for matriz in con_descriptores:
        df[np.unique(codebook.assign(matriz))] += 1

    documentos = len(con_descriptores)
    return Vocabulary(codebook, inverse_document_frequency(df, documentos), documentos)


def describe(path: str | Path, **opciones: object) -> np.ndarray:
    """Descriptores locales del objeto, eligiendo el pipeline por su extension.

    Es la parte de ``ExtractFeatures`` que decide si el objeto es una imagen
    o un audio. Los extractores se importan aqui dentro y no arriba porque
    cada uno arrastra su dependencia: describir un audio no deberia exigir
    tener OpenCV instalado, ni al reves.
    """

    ruta = Path(path)
    extension = ruta.suffix.lower()
    if extension in EXTENSIONES_DE_IMAGEN:
        from engine.multimedia.sift import extract_sift

        return extract_sift(ruta, **opciones)  # type: ignore[arg-type]
    if extension in EXTENSIONES_DE_AUDIO:
        from engine.multimedia.mfcc import extract_mfcc

        return extract_mfcc(ruta, **opciones)  # type: ignore[arg-type]
    raise FeatureExtractionError(
        f"{ruta.name!r} no es una imagen ni un audio reconocido; se esperaba una de "
        f"{', '.join(sorted(EXTENSIONES_DE_IMAGEN | EXTENSIONES_DE_AUDIO))}",
        ruta,
    )


def extract_features(
    path: str | Path,
    vocabulary: Vocabulary,
    *,
    variant: str = TF_LOG,
    **opciones: object,
) -> np.ndarray:
    """``ExtractFeatures(objeto)``: el vector de dimension K que se indexa.

    Recibe una imagen o un audio, decide el pipeline por el tipo y devuelve
    siempre un vector de la misma dimension, normalizado a norma 1.

    Un objeto del que no sale ningun descriptor -- una imagen de un solo
    color, un audio mas corto que una ventana -- devuelve el **vector nulo**,
    no un error y no una division por cero (criterio 4 de #181). Y hay que
    saber lo que eso significa: bajo similitud del coseno el vector nulo
    queda a distancia 1 de todo, incluido de si mismo, asi que esos objetos
    no son alcanzables por una busqueda y conviene no indexarlos.
    ``tiene_contenido`` sirve para filtrarlos al armar el dataset.

    Un archivo **roto** no entra en ese caso y sigue siendo un error: lo
    distingue ``EmptyObjectError``, que los extractores lanzan solo cuando
    leyeron bien y no habia nada.
    """

    try:
        descriptores = describe(Path(path), **opciones)
    except EmptyObjectError:
        # El archivo se leyo bien y no hay nada que describir. Cualquier otro
        # FeatureExtractionError -- archivo roto, formato desconocido, ruta
        # ilegible -- sigue subiendo: un .jpg a medio descargar no es una
        # foto en blanco.
        return np.zeros(vocabulary.k, dtype=np.float32)
    return vocabulary.histogram(descriptores, variant=variant)


def tiene_contenido(vector: np.ndarray) -> bool:
    """Si el vector describe algo. El nulo no: ver ``extract_features``."""

    return bool(np.any(np.asarray(vector) != 0.0))


def _normalizar(pesos: np.ndarray) -> np.ndarray:
    """Lleva el vector a norma 1, o lo deja en cero si no tiene nada que normalizar.

    Aqui es donde estaria la division por cero que el criterio 4 prohibe: un
    objeto cuyas palabras tengan todas idf 0 da un vector de puros ceros, y
    dividir por su norma seria 0/0.
    """

    norma = float(np.sqrt(np.sum(pesos * pesos)))
    if norma == 0.0:
        return np.zeros(len(pesos), dtype=np.float32)
    return (pesos / norma).astype(np.float32)


__all__ = [
    "EXTENSIONES_DE_AUDIO",
    "EXTENSIONES_DE_IMAGEN",
    "FORMATO",
    "TF_LOG",
    "TF_RAW",
    "Vocabulary",
    "VocabularyError",
    "build_vocabulary",
    "describe",
    "extract_features",
    "tiene_contenido",
]
