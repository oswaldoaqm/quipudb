"""Cargar el dataset de rostros en QuipuDB (#302).

Lee el manifiesto de #301 y arma las sentencias que meten cada persona y cada
rostro en el motor. No importa ``engine``: todo va por HTTP.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from app.carga.esquema import PERSONAS, ROSTROS, sentencias
from app.dataset.lfw import DATOS, Rostro
from app.motor import ClienteMotor, literal, literal_vector
from app.rostros.deteccion import crear_detector
from app.rostros.embedding import crear_descriptor, vectores_de


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


@dataclass(frozen=True, slots=True)
class Informe:
    """Que entro."""

    personas: int = 0
    rostros: int = 0


def cargar(
    cliente: ClienteMotor,
    rostros: Iterable[Rostro],
    *,
    datos: Path = DATOS,
    detector=None,
    descriptor=None,
    log: Callable[[str], None] = lambda _: None,
) -> Informe:
    """Inserta las personas del manifiesto y el vector de cada una de sus fotos."""

    if detector is None:
        detector = crear_detector()
    if descriptor is None:
        descriptor = crear_descriptor()

    agrupado = por_persona(rostros)
    personas = rostros_cargados = 0
    siguiente_rostro = 1

    for persona_id, (nombre, suyos) in enumerate(agrupado.items(), start=1):
        vectores = []
        for rostro in suyos:
            # Una foto con dos caras no dice cual es la persona del manifiesto;
            # se toma la mas confiable, que `detectar` deja primera.
            sacados = vectores_de(rostro.archivo(datos), detector, descriptor)
            vectores.append((rostro.ruta, list(sacados[0])))

        cliente.query(insertar_persona(persona_id, nombre, suyos[0].origen, len(vectores)))
        for ruta, vector in vectores:
            cliente.query(insertar_rostro(siguiente_rostro, persona_id, ruta, vector))
            siguiente_rostro += 1
        personas += 1
        rostros_cargados += len(vectores)
        log(f"{nombre}: {len(vectores)} rostros")

    return Informe(personas=personas, rostros=rostros_cargados)


__all__ = [
    "Informe",
    "cargar",
    "crear_esquema",
    "esquema_existe",
    "insertar_persona",
    "insertar_rostro",
    "por_persona",
]
