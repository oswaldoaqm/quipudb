"""Elegir el umbral con datos, no a ojo (#303).

El umbral decide si el vecino mas cercano cuenta como la misma persona. Hay
dos formas de equivocarse y tiran en direcciones opuestas:

- **umbral muy alto**: acepta a cualquiera, y un desconocido sale
  identificado como la persona que casualmente quedo mas cerca (falso
  positivo);
- **umbral muy bajo**: rechaza a los que si estan, y alguien cargado sale
  como desconocido (falso negativo).

Por eso no se elige mirando una foto: se barre el rango y se mira la curva.

El barrido no pasa por el motor. Compara los vectores a fuerza bruta con
numpy, a proposito: asi mide **el umbral**, sin mezclarlo con lo que el
indice aproximado pueda o no encontrar. HNSW tiene Recall@10 de 0.99, asi que
el umbral elegido aqui vale igual al consultar con el indice, y si alguna vez
dejara de valer conviene saber cual de las dos cosas cambio.

"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

import numpy as np

from app.dataset.lfw import Rostro

MINIMO_FOTOS = 3
"""Fotos minimas por identidad para que entre al experimento.

Hacen falta al menos dos para la base y una para consultar. Con tres, la
medicion no depende de una sola foto buena o mala.
"""


@dataclass(frozen=True, slots=True)
class Reparto:
    """El dataset partido para medir, sin que una foto este en los dos lados."""

    base: dict[str, list[np.ndarray]]
    """Los vectores que estarian cargados en QuipuDB."""

    consultas: list[tuple[str, np.ndarray]]
    """``(persona, vector)`` de alguien que **si** esta en la base."""

    intrusos: list[tuple[str, np.ndarray]]
    """``(persona, vector)`` de alguien que **no** esta: tiene que salir desconocido."""


def repartir(
    rostros: Iterable[Rostro],
    vectores: dict[str, np.ndarray],
    *,
    identidades: int | None = None,
    proporcion_intrusos: float = 0.3,
    minimo: int = MINIMO_FOTOS,
) -> Reparto:
    """Parte el dataset en base, consultas e intrusos.

    Una foto nunca esta en la base y en las consultas a la vez: si lo
    estuviera, la distancia seria 0 y el experimento diria que todo funciona.
    """

    por_persona: dict[str, list[np.ndarray]] = {}
    for rostro in rostros:
        vector = vectores.get(rostro.ruta)
        if vector is not None:
            por_persona.setdefault(rostro.persona, []).append(vector)

    utiles = sorted(p for p, v in por_persona.items() if len(v) >= minimo)
    if identidades:
        utiles = utiles[:identidades]
    if len(utiles) < 2:
        raise ValueError(
            f"hacen falta al menos 2 identidades con {minimo} fotos y hay {len(utiles)}"
        )

    # Con una proporcion positiva siempre se reserva al menos uno: sin
    # intrusos no hay como medir falsos positivos, que es la mitad del
    # problema. Pedir 0 explicitamente si deja el experimento sin ninguno.
    if proporcion_intrusos <= 0:
        cuantos_intrusos = 0
    else:
        cuantos_intrusos = max(1, int(len(utiles) * proporcion_intrusos))
    nombres_intrusos = set(utiles[:cuantos_intrusos])

    base: dict[str, list[np.ndarray]] = {}
    consultas: list[tuple[str, np.ndarray]] = []
    intrusos: list[tuple[str, np.ndarray]] = []

    for persona in utiles:
        suyos = por_persona[persona]
        if persona in nombres_intrusos:
            # No entra a la base: sus fotos son las que tienen que rechazarse.
            intrusos.extend((persona, v) for v in suyos)
            continue
        # La ultima se reserva para consultar; el resto se "carga".
        base[persona] = suyos[:-1]
        consultas.append((persona, suyos[-1]))

    return Reparto(base=base, consultas=consultas, intrusos=intrusos)


def acotar(
    rostros: Sequence[Rostro],
    identidades: int | None = None,
    minimo: int = MINIMO_FOTOS,
) -> list[Rostro]:
    """Se queda con las imagenes de las primeras ``identidades`` utiles.

    Se filtra **antes** de sacar los vectores: con 4324 imagenes en el
    manifiesto, calcular las de las 118 identidades que el experimento no va a
    mirar son varios minutos tirados.
    """

    cuenta: dict[str, int] = {}
    for rostro in rostros:
        cuenta[rostro.persona] = cuenta.get(rostro.persona, 0) + 1

    utiles = sorted(p for p, n in cuenta.items() if n >= minimo)
    if identidades:
        utiles = utiles[:identidades]
    elegidas = set(utiles)
    return [rostro for rostro in rostros if rostro.persona in elegidas]


__all__ = ["MINIMO_FOTOS", "Reparto", "acotar", "repartir"]
