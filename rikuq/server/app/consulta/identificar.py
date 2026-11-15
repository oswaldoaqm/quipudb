"""Consultar los rostros mas parecidos (#303).

El motor acepta un ``VECTOR(...)`` calculado fuera (#251), que es lo que hace
falta aqui: el vector lo produce ArcFace en #305, no el vocabulario visual
de la Parte 4. Si se le pasara el archivo, el motor extraeria SIFT y
compararia manzanas con naranjas.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.carga.esquema import PERSONAS, ROSTROS
from app.motor import ClienteMotor, literal_vector

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


__all__ = ["VECINOS", "Candidato", "consulta_sql", "distancia_de", "nombres_de", "vecinos"]
