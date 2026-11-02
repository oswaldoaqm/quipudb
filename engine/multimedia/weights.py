"""Pesos TF-IDF del histograma de palabras visuales y auditivas (#181).

Es el espejo en Python de ``core/include/quipudb/index/text_weights.hpp``, y
vive en su propio modulo por la misma razon que alla: las formulas las usan
el constructor del vocabulario y el histograma de cada objeto, y si cada uno
tuviera su copia bastaria con que una cambiara para que los pesos dejaran de
significar lo mismo sin que nada lo denunciara.

    idf(w) = log10(N / df_w)        tf_w = 1 + log10(tf)  |  tf
    peso   = tf_w(tf) * idf(w)

Son las de la diapositiva, sin suavizado: una palabra que esta en todos los
objetos tiene idf 0 y no pesa, porque no distingue a ninguno. Que sean las
mismas que las de la busqueda de texto no es cosmetico: si el histograma
visual pesara distinto que el textual, los numeros del informe no se podrian
comparar entre las partes 3 y 4.
"""

from __future__ import annotations

import numpy as np

TF_LOG = "log"
TF_RAW = "raw"
"""Las dos variantes de ``tf_weight`` de la Parte 3. Por omision ``log``: diez
apariciones de una palabra no hacen al objeto diez veces mas parecido."""


class WeightError(ValueError):
    """La variante de tf pedida no existe."""


def tf_weight(counts: np.ndarray, variant: str = TF_LOG) -> np.ndarray:
    """Peso del tf, componente a componente. Espejo de ``tf_weight`` en C++.

    Una palabra que no aparece pesa 0, no ``1 + log10(0) = -inf``. Sin esa
    guarda, una sola palabra ausente deja el vector entero inservible.
    """

    if variant not in (TF_LOG, TF_RAW):
        raise WeightError(f"variante de tf desconocida: {variant!r}")
    frecuencias = np.asarray(counts, dtype=np.float64)
    if variant == TF_RAW:
        return frecuencias
    return np.where(frecuencias > 0, 1.0 + np.log10(np.maximum(frecuencias, 1.0)), 0.0)


def inverse_document_frequency(document_frequency: np.ndarray, documents: int) -> np.ndarray:
    """``log10(N / df)``. Espejo de ``idf`` en C++.

    Una palabra que no le toco a ningun objeto pesa 0 en vez de dividir por
    cero, y una que esta en todos tambien: no distingue a ninguno.
    """

    df = np.asarray(document_frequency, dtype=np.float64)
    if documents <= 0:
        return np.zeros_like(df)
    return np.where(df > 0, np.log10(documents / np.maximum(df, 1.0)), 0.0)


__all__ = [
    "TF_LOG",
    "TF_RAW",
    "WeightError",
    "inverse_document_frequency",
    "tf_weight",
]
