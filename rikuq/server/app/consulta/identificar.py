"""Identificar a quien pertenece un rostro (#303).

Paso 4 de la lamina de la clase 10: *Comparison & Recognition*, con su
*Threshold* y su *Distance Metric* decidiendo MATCH o NO-MATCH.

Lo importante de este paso es lo que **no** hace: el vecino mas cercano
siempre existe. Sin umbral, una cara que nunca se cargo se identificaria como
la persona que casualmente quedo mas cerca, y el sistema no diria nunca
"no se quien es". Por eso la respuesta puede ser ``None``.

El motor acepta un ``VECTOR(...)`` calculado fuera (#251), que es lo que hace
falta aqui: el vector lo produce ArcFace en #305, no el vocabulario visual
de la Parte 4. Si se le pasara el archivo, el motor extraeria SIFT y
compararia manzanas con naranjas.

Uso, desde ``rikuq/server``::

    python -m app.consulta.identificar foto.jpg
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.carga.esquema import PERSONAS, ROSTROS
from app.motor import ClienteMotor, literal_vector

UMBRAL_POR_DEFECTO = 0.55
"""Distancia coseno maxima para dar una identificacion por buena.

**Medido**, no elegido a ojo: sale del barrido de `app.consulta.umbral` sobre
40 identidades de LFW (28 cargadas y 12 como intrusos, 309 fotos ajenas).

| umbral | precision | falsos positivos |
|---|---|---|
| 0.45 | 89.3% | 0 de 309 |
| **0.55** | **92.9%** | **0 de 309** |
| 0.65 | 92.9% | 0 de 309 |
| 0.70 | 92.9% | 4 de 309 |
| 0.80 | 92.9% | 173 de 309 |

De 0.55 a 0.65 el resultado es el mismo; se toma el extremo estricto porque
equivocarse de persona es peor que no reconocerla. A partir de 0.70 empiezan
los falsos positivos y a 0.85 el sistema ya le pone nombre a cualquiera.

El valor es de **ArcFace**: con SFace salia 0.45. Cambiar de descriptor cambia
el espacio, asi que hay que volver a medir, no reusar el numero.

Para rehacer la medicion: ``python -m app.consulta.umbral --identidades 40``.
"""

VECINOS = 5
"""Cuantos candidatos pedirle al indice.

Con uno bastaria para decidir, pero traer varios deja ver si el segundo esta
cerca del primero -- señal de que la foto es ambigua -- y cuesta lo mismo.
"""


@dataclass(frozen=True, slots=True)
class Candidato:
    """Un rostro cargado que se parece al que se consulta."""

    rostro_id: int
    persona_id: int
    ruta: str
    distancia: float
    persona: str | None = None


@dataclass(frozen=True, slots=True)
class Identificacion:
    """La respuesta: quien es, o que no se sabe."""

    persona: str | None
    distancia: float
    candidatos: tuple[Candidato, ...] = ()

    @property
    def conocido(self) -> bool:
        return self.persona is not None


def consulta_sql(vector: Sequence[float], k: int = VECINOS) -> str:
    """El SELECT con SIMILAR_TO contra el indice HNSW.

    ``SIMILARITY_SCORE()`` con metrica coseno devuelve una **similitud**
    (mayor es mas parecido), no una distancia; se convierte al leerla.
    """

    return (
        f"SELECT id, persona_id, ruta, SIMILARITY_SCORE() AS puntaje FROM {ROSTROS} "
        f"WHERE SIMILAR_TO(embedding, {literal_vector(vector)}, k={k}) "
        "USING HNSW WITH METRIC=cosine"
    )


def distancia_de(puntaje: float) -> float:
    """Pasa la similitud coseno del motor a distancia: menor es mas parecido.

    Con vectores unitarios el coseno vive en [-1, 1], asi que la distancia
    queda en [0, 2], que es la misma escala que usa `app.rostros.embedding`.
    """

    return 1.0 - float(puntaje)


def vecinos(cliente: ClienteMotor, vector: Sequence[float], k: int = VECINOS) -> list[Candidato]:
    """Los k rostros mas cercanos, el mas parecido primero."""

    filas = cliente.query(consulta_sql(vector, k)).dicts()
    candidatos = [
        Candidato(
            rostro_id=int(fila["id"]),
            persona_id=int(fila["persona_id"]),
            ruta=str(fila["ruta"]),
            distancia=distancia_de(fila["puntaje"]),
        )
        for fila in filas
    ]
    return sorted(candidatos, key=lambda c: c.distancia)


def nombres_de(cliente: ClienteMotor, ids: Sequence[int]) -> dict[int, str]:
    """El nombre de cada persona, en una consulta por id.

    SIMILAR_TO no admite JOIN (ADR 0008), asi que el nombre se trae aparte.
    Son a lo sumo `k` ids distintos y el indice hash los resuelve en dos
    paginas cada uno, igual que hace Quipucamayoc con sus papers.
    """

    nombres: dict[int, str] = {}
    for persona_id in dict.fromkeys(ids):
        filas = cliente.query(
            f"SELECT id, nombre FROM {PERSONAS} WHERE id = {int(persona_id)}"
        ).dicts()
        if filas:
            nombres[int(persona_id)] = str(filas[0]["nombre"])
    return nombres


def identificar(
    cliente: ClienteMotor,
    vector: Sequence[float],
    *,
    umbral: float = UMBRAL_POR_DEFECTO,
    k: int = VECINOS,
) -> Identificacion:
    """Dice de quien es el rostro, o que no lo sabe."""

    candidatos = vecinos(cliente, vector, k)
    if not candidatos:
        return Identificacion(persona=None, distancia=float("inf"))

    nombres = nombres_de(cliente, [c.persona_id for c in candidatos])
    candidatos = [
        Candidato(c.rostro_id, c.persona_id, c.ruta, c.distancia, nombres.get(c.persona_id))
        for c in candidatos
    ]
    mejor = candidatos[0]

    if mejor.distancia > umbral:
        # El vecino mas cercano siempre existe; que este cerca es otra cosa.
        return Identificacion(
            persona=None,
            distancia=mejor.distancia,
            candidatos=tuple(candidatos),
        )

    return Identificacion(
        persona=mejor.persona,
        distancia=mejor.distancia,
        candidatos=tuple(candidatos),
    )


__all__ = [
    "UMBRAL_POR_DEFECTO",
    "VECINOS",
    "Candidato",
    "Identificacion",
    "consulta_sql",
    "distancia_de",
    "identificar",
    "nombres_de",
    "vecinos",
]
