"""K-Means y el libro de palabras (codebook) del Bag of Words.

Es el paso intermedio del pipeline de la seccion 2.4.1. SIFT (#176) y MFCC
(#177) describen un objeto con una matriz de N x D, con N distinto en cada
objeto, y por eso dos objetos no se pueden comparar restando sus matrices.
K-Means agrupa TODOS los descriptores del dataset en K centroides, que pasan
a ser las "palabras" visuales o auditivas. Cada descriptor se reemplaza por
la palabra mas cercana, y asi un objeto se vuelve un conjunto de palabras
que #181 convierte en un histograma de dimension fija K.

El codebook es, entonces, el diccionario compartido. Y de ahi sale la regla
que sostiene todo lo demas: **una consulta tiene que cuantizarse con el
mismo codebook que el dataset**. Con otro codebook la palabra 7 significa
otra cosa, los histogramas dejan de ser comparables y la busqueda devuelve
resultados sin sentido sin avisar de nada. Por eso el codebook se guarda en
disco con un identificador y quien lo usa puede comprobar que es el mismo.

Este modulo no sabe nada de imagenes ni de audio: trabaja sobre una matriz
de N x D cualquiera. Es a proposito, porque el IVF de #183 necesita K-Means
sobre vectores completos y conviene que sea esta misma implementacion y no
otra con los mismos errores.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import numpy as np

FORMATO = 1
"""Version del formato en disco del codebook. Si cambia la forma de guardarlo,
sube, y ``Codebook.load`` rechaza lo viejo en vez de leerlo mal."""

MAX_ITERACIONES = 100
TOLERANCIA = 1e-4
"""Cuanto puede moverse el centroide que mas se movio para dar por convergido.
Es sobre la distancia, no sobre la inercia: una inercia que baja poco no
quiere decir que los centroides se hayan quedado quietos."""

BLOQUE = 8192
"""Descriptores por bloque al asignar. Calcular las distancias de un millon
de descriptores contra 256 centroides de una sola vez pediria gigabytes; por
bloques el pico de memoria no depende del tamano del dataset."""


class CodebookError(ValueError):
    """El codebook no se puede construir, leer o aplicar."""


@dataclass(frozen=True, slots=True)
class Codebook:
    """Las K palabras del vocabulario, con que semilla salieron y su huella.

    ``identifier`` es la huella de los centroides. No es decoracion: es lo
    unico que permite detectar que una consulta se cuantizo con un codebook
    distinto al del dataset, que es un error silencioso -- los histogramas
    salen, tienen la dimension correcta y describen otra cosa.
    """

    centroids: np.ndarray
    seed: int
    identifier: str

    @property
    def k(self) -> int:
        return int(self.centroids.shape[0])

    @property
    def dim(self) -> int:
        return int(self.centroids.shape[1])

    def assign(self, descriptors: np.ndarray) -> np.ndarray:
        """Devuelve, por cada descriptor, el indice de su centroide mas cercano."""

        datos = _como_matriz(descriptors)
        if datos.shape[1] != self.dim:
            raise CodebookError(
                f"los descriptores tienen dimension {datos.shape[1]} y el codebook "
                f"espera {self.dim}"
            )
        return _asignar(datos, self.centroids)

    def save(self, path: str | Path) -> None:
        """Guarda el codebook con su huella y la version del formato."""

        ruta = Path(path)
        ruta.parent.mkdir(parents=True, exist_ok=True)
        with ruta.open("wb") as salida:
            np.savez(
                salida,
                formato=np.asarray(FORMATO),
                centroids=self.centroids,
                seed=np.asarray(self.seed),
                identifier=np.asarray(self.identifier),
            )

    @classmethod
    def load(cls, path: str | Path) -> Codebook:
        """Lee un codebook y comprueba que no venga alterado."""

        ruta = Path(path)
        try:
            with np.load(ruta, allow_pickle=False) as datos:
                formato = int(datos["formato"])
                centroides = np.ascontiguousarray(datos["centroids"], dtype=np.float32)
                semilla = int(datos["seed"])
                huella = str(datos["identifier"])
        except (OSError, ValueError, KeyError) as error:
            raise CodebookError(f"no se puede leer el codebook {ruta.name!r}: {error}") from None

        if formato != FORMATO:
            raise CodebookError(
                f"el codebook {ruta.name!r} es del formato {formato} y este motor lee "
                f"el {FORMATO}"
            )
        # La huella se recalcula en vez de creerle a la guardada: un archivo
        # a medio escribir o editado a mano daria centroides plausibles.
        esperada = huella_de(centroides)
        if esperada != huella:
            raise CodebookError(
                f"el codebook {ruta.name!r} no coincide con su huella: "
                f"dice {huella!r} y sus centroides dan {esperada!r}"
            )
        return cls(centroides, semilla, huella)


def huella_de(centroids: np.ndarray) -> str:
    """Huella de un conjunto de centroides, estable entre maquinas.

    Entra la forma ademas de los bytes: dos codebooks con los mismos valores
    y distinta K no son el mismo vocabulario, y ``tobytes`` no distingue una
    matriz de 2 x 4 de una de 4 x 2.
    """

    datos = np.ascontiguousarray(centroids, dtype=np.float32)
    resumen = hashlib.blake2b(digest_size=8)
    resumen.update(f"{datos.shape[0]}x{datos.shape[1]}:".encode())
    resumen.update(datos.tobytes())
    return resumen.hexdigest()


def build_codebook(
    descriptors: np.ndarray,
    k: int,
    *,
    seed: int = 0,
    max_descriptors: int | None = None,
    max_iterations: int = MAX_ITERACIONES,
    tolerance: float = TOLERANCIA,
) -> Codebook:
    """Agrupa los descriptores en ``k`` centroides y devuelve el codebook.

    Con la misma semilla y los mismos datos el resultado es identico, que es
    el criterio 1 del issue: si no lo fuera, reconstruir el dataset cambiaria
    el significado de cada palabra y habria que reindexar todo.

    ``max_descriptors`` toma una muestra al azar -- con la misma semilla --
    cuando hay mas descriptores de los que conviene cargar. Es el criterio 3:
    100 000 imagenes a 500 descriptores cada una son 50 millones de vectores
    de 128 valores, unos 25 GB. El vocabulario sale practicamente igual con
    una muestra, porque los centroides describen zonas densas del espacio y
    esas zonas no desaparecen al muestrear.
    """

    datos = _como_matriz(descriptors)
    if k < 1:
        raise CodebookError(f"k tiene que ser positivo, y es {k}")
    if max_iterations < 1:
        raise CodebookError(f"max_iterations tiene que ser positivo, y es {max_iterations}")
    if max_descriptors is not None and max_descriptors < k:
        raise CodebookError(
            f"max_descriptors ({max_descriptors}) no puede ser menor que k ({k})"
        )

    azar = np.random.default_rng(seed)
    if max_descriptors is not None and len(datos) > max_descriptors:
        elegidos = azar.choice(len(datos), size=max_descriptors, replace=False)
        datos = datos[np.sort(elegidos)]

    if len(datos) < k:
        raise CodebookError(
            f"hacen falta al menos {k} descriptores para {k} centroides, y hay {len(datos)}"
        )

    centroides = _inicializar(datos, k, azar)
    for _ in range(max_iterations):
        etiquetas = _asignar(datos, centroides)
        nuevos = _promediar(datos, etiquetas, centroides, azar)
        movimiento = float(np.sqrt(((nuevos - centroides) ** 2).sum(axis=1)).max())
        centroides = nuevos
        if movimiento <= tolerance:
            break

    return Codebook(centroides, seed, huella_de(centroides))


def _como_matriz(descriptors: np.ndarray) -> np.ndarray:
    datos = np.asarray(descriptors, dtype=np.float32)
    if datos.ndim != 2:
        raise CodebookError(f"se esperaba una matriz de N x D y llego de {datos.ndim} dimensiones")
    if datos.shape[0] == 0 or datos.shape[1] == 0:
        raise CodebookError("no hay descriptores de los que sacar un vocabulario")
    if not np.isfinite(datos).all():
        raise CodebookError("hay descriptores con infinitos o NaN")
    return datos


def _inicializar(datos: np.ndarray, k: int, azar: np.random.Generator) -> np.ndarray:
    """Siembra los centroides con k-means++.

    Al azar puro, dos centroides pueden caer en el mismo grupo y dejar otro
    sin representante: el vocabulario queda con palabras repetidas y con
    zonas del espacio sin ninguna. k-means++ elige cada centroide con
    probabilidad proporcional al cuadrado de su distancia al mas cercano ya
    elegido, asi que tienden a repartirse.
    """

    centroides = np.empty((k, datos.shape[1]), dtype=np.float32)
    centroides[0] = datos[azar.integers(len(datos))]
    # En float64 y no float32: `choice` exige que las probabilidades sumen
    # exactamente 1, y con cientos de miles de descriptores el error de
    # redondeo de float32 basta para que las rechace.
    cercania = ((datos - centroides[0]) ** 2).sum(axis=1, dtype=np.float64)
    for indice in range(1, k):
        total = float(cercania.sum())
        if total <= 0.0:
            # Todos los puntos restantes coinciden con algun centroide: no
            # hay nada que repartir y se completa con puntos cualesquiera.
            centroides[indice:] = datos[azar.integers(len(datos), size=k - indice)]
            break
        centroides[indice] = datos[azar.choice(len(datos), p=cercania / total)]
        cercania = np.minimum(
            cercania, ((datos - centroides[indice]) ** 2).sum(axis=1, dtype=np.float64)
        )
    return centroides


def _asignar(datos: np.ndarray, centroides: np.ndarray) -> np.ndarray:
    """Indice del centroide mas cercano de cada fila, por bloques."""

    normas = (centroides**2).sum(axis=1)
    etiquetas = np.empty(len(datos), dtype=np.int32)
    for inicio in range(0, len(datos), BLOQUE):
        trozo = datos[inicio : inicio + BLOQUE]
        # ||x - c||^2 = ||x||^2 - 2 x.c + ||c||^2; el ||x||^2 no cambia el
        # minimo sobre c, asi que no se calcula.
        distancias = normas - 2.0 * (trozo @ centroides.T)
        etiquetas[inicio : inicio + len(trozo)] = np.argmin(distancias, axis=1)
    return etiquetas


def _promediar(
    datos: np.ndarray,
    etiquetas: np.ndarray,
    centroides: np.ndarray,
    azar: np.random.Generator,
) -> np.ndarray:
    """Recoloca cada centroide en el promedio de los puntos que le tocaron.

    Un centroide puede quedarse sin ningun punto. Dejarlo donde esta lo
    congela para siempre y el vocabulario pierde una palabra en silencio, asi
    que se lo manda al punto peor representado del dataset, que es donde mas
    falta hace.
    """

    k, dimension = centroides.shape
    sumas = np.zeros((k, dimension), dtype=np.float64)
    np.add.at(sumas, etiquetas, datos)
    cuentas = np.bincount(etiquetas, minlength=k)

    nuevos = centroides.astype(np.float64)
    ocupados = cuentas > 0
    nuevos[ocupados] = sumas[ocupados] / cuentas[ocupados, None]

    vacios = np.flatnonzero(~ocupados)
    if len(vacios):
        lejania = ((datos - nuevos[etiquetas].astype(np.float32)) ** 2).sum(axis=1)
        peores = np.argsort(-lejania)[: len(vacios)]
        if len(peores) < len(vacios):
            peores = azar.integers(len(datos), size=len(vacios))
        nuevos[vacios] = datos[peores]
    return nuevos.astype(np.float32)


__all__ = [
    "BLOQUE",
    "FORMATO",
    "MAX_ITERACIONES",
    "TOLERANCIA",
    "Codebook",
    "CodebookError",
    "build_codebook",
    "huella_de",
]
