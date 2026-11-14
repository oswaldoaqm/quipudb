"""Cargar el dataset de rostros en QuipuDB (#302).

Lee el manifiesto de #301 y arma las sentencias que meten cada persona y cada
rostro en el motor. No importa ``engine``: todo va por HTTP.
"""

from __future__ import annotations

from collections.abc import Iterable

from app.carga.esquema import PERSONAS, ROSTROS, sentencias
from app.dataset.lfw import Rostro
from app.motor import ClienteMotor, literal, literal_vector


def crear_esquema(cliente: ClienteMotor) -> None:
    """Crea las dos tablas y sus tres indices, en orden."""

    for sentencia in sentencias():
        cliente.query(sentencia)


def esquema_existe(cliente: ClienteMotor) -> bool:
    return {PERSONAS, ROSTROS} <= cliente.table_names()


def insertar_persona(persona_id: int, nombre: str, origen: str, fotos: int) -> str:
    return (
        f"INSERT INTO {PERSONAS} (id, nombre, origen, fotos) VALUES "
        f"({persona_id}, {literal(nombre)}, {literal(origen)}, {fotos})"
    )


def insertar_rostro(rostro_id: int, persona_id: int, ruta: str, vector: Iterable[float]) -> str:
    return (
        f"INSERT INTO {ROSTROS} (id, persona_id, ruta, embedding) VALUES "
        f"({rostro_id}, {persona_id}, {literal(ruta)}, {literal_vector(list(vector))})"
    )


def por_persona(rostros: Iterable[Rostro]) -> dict[str, list[Rostro]]:
    """Agrupa el manifiesto por persona, conservando el orden de aparicion."""

    agrupado: dict[str, list[Rostro]] = {}
    for rostro in rostros:
        agrupado.setdefault(rostro.persona, []).append(rostro)
    return agrupado


__all__ = ["crear_esquema", "esquema_existe", "insertar_persona", "insertar_rostro", "por_persona"]
