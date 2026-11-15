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

# Hasta donde barrer. Mas alla de 1.0 el umbral acepta a casi cualquiera:
# entre personas distintas la distancia ronda 0.95-1.06.
PASOS = tuple(round(0.05 * i, 2) for i in range(1, 25))

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


@dataclass(frozen=True, slots=True)
class Punto:
    """Como le fue al sistema con un umbral concreto."""

    umbral: float
    aciertos: int
    confusiones: int
    """Estaba en la base, pero se le puso el nombre de otra persona."""
    rechazos: int
    """Estaba en la base y salio desconocido."""
    intrusos_rechazados: int
    falsos_positivos: int
    """No estaba en la base y se le puso un nombre."""

    @property
    def consultas(self) -> int:
        return self.aciertos + self.confusiones + self.rechazos

    @property
    def intrusos(self) -> int:
        return self.intrusos_rechazados + self.falsos_positivos

    @property
    def precision(self) -> float:
        return self.aciertos / self.consultas if self.consultas else 0.0

    @property
    def tasa_falsos_positivos(self) -> float:
        return self.falsos_positivos / self.intrusos if self.intrusos else 0.0

    @property
    def balance(self) -> float:
        """Precision y rechazo de intrusos pesan igual.

        Promediar las dos y no sumar aciertos a secas evita el umbral que
        acierta mucho porque acepta todo: ese tiene precision alta y rechazo
        de intrusos nulo.
        """

        correcto_intrusos = 1.0 - self.tasa_falsos_positivos
        return (self.precision + correcto_intrusos) / 2


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


def _mas_cercano(base: dict[str, list[np.ndarray]], vector: np.ndarray) -> tuple[str, float]:
    """El nombre mas cercano y su distancia coseno, a fuerza bruta."""

    mejor_nombre, mejor = "", float("inf")
    for persona, suyos in base.items():
        distancias = 1.0 - np.asarray(suyos, dtype=np.float32) @ vector
        minima = float(distancias.min())
        if minima < mejor:
            mejor_nombre, mejor = persona, minima
    return mejor_nombre, mejor


def medir(reparto: Reparto, pasos: Sequence[float] = PASOS) -> list[Punto]:
    """La curva: como le va al sistema con cada umbral."""

    # Las distancias se calculan una vez y se reutilizan en todo el barrido.
    conocidos = [
        (persona, *_mas_cercano(reparto.base, vector)) for persona, vector in reparto.consultas
    ]
    ajenos = [_mas_cercano(reparto.base, vector)[1] for _, vector in reparto.intrusos]

    curva = []
    for umbral in pasos:
        aciertos = confusiones = rechazos = 0
        for esperado, propuesto, distancia in conocidos:
            if distancia > umbral:
                rechazos += 1
            elif propuesto == esperado:
                aciertos += 1
            else:
                confusiones += 1
        falsos = sum(1 for distancia in ajenos if distancia <= umbral)
        curva.append(
            Punto(
                umbral=umbral,
                aciertos=aciertos,
                confusiones=confusiones,
                rechazos=rechazos,
                intrusos_rechazados=len(ajenos) - falsos,
                falsos_positivos=falsos,
            )
        )
    return curva


__all__ = ["MINIMO_FOTOS", "PASOS", "Punto", "Reparto", "acotar", "medir", "repartir"]
