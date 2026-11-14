"""Cargar el dataset de rostros en QuipuDB (#302).

Lee el manifiesto de #301, saca el vector de cada imagen con el pipeline de
#304 y #305, y lo inserta por el API REST del motor. No importa ``engine``:
todo va por HTTP.

Una imagen sin rostro no corta la carga. De 4324 fotos de prensa siempre hay
alguna donde YuNet no encuentra nada, y perder la carga entera por una sola
obligaria a empezar de cero. Se anota, se sigue, y al final se dice cuantas
entraron y cuantas no.

"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from app.carga.esquema import PERSONAS, ROSTROS, sentencias
from app.dataset.lfw import DATOS, Rostro
from app.motor import ClienteMotor, literal, literal_vector
from app.rostros.deteccion import crear_detector
from app.rostros.embedding import crear_descriptor, vectores_de
from app.rostros.errores import FeatureExtractionError


@dataclass(frozen=True, slots=True)
class Informe:
    """Que entro y que no."""

    personas: int = 0
    rostros: int = 0
    sin_rostro: tuple[tuple[str, str], ...] = field(default=())
    """``(ruta, motivo)`` de las imagenes que no aportaron ningun vector."""

    @property
    def fallidas(self) -> int:
        return len(self.sin_rostro)

    def resumen(self) -> str:
        lineas = [
            f"{self.personas} personas y {self.rostros} rostros cargados",
            f"{self.fallidas} imagenes sin rostro utilizable",
        ]
        lineas.extend(f"  {ruta}: {motivo}" for ruta, motivo in self.sin_rostro[:10])
        if self.fallidas > 10:
            lineas.append(f"  ... y {self.fallidas - 10} mas")
        return "\n".join(lineas)


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


def cargar(
    cliente: ClienteMotor,
    rostros: Iterable[Rostro],
    *,
    datos: Path = DATOS,
    detector=None,
    descriptor=None,
    log: Callable[[str], None] = lambda _: None,
) -> Informe:
    """Inserta las personas y sus rostros. Devuelve que entro y que no."""

    if detector is None:
        detector = crear_detector()
    if descriptor is None:
        descriptor = crear_descriptor()

    agrupado = por_persona(rostros)
    sin_rostro: list[tuple[str, str]] = []
    pendientes: list[str] = []
    personas = rostros_cargados = 0
    siguiente_rostro = 1

    for persona_id, (nombre, suyos) in enumerate(agrupado.items(), start=1):
        vectores: list[tuple[str, list[float]]] = []
        for rostro in suyos:
            try:
                sacados = vectores_de(rostro.archivo(datos), detector, descriptor)
            except FeatureExtractionError as error:
                sin_rostro.append((rostro.ruta, str(error)))
                continue
            # Una foto con dos caras no dice cual es la persona del manifiesto;
            # se toma la mas confiable, que `detectar` deja primera.
            vectores.append((rostro.ruta, list(sacados[0])))

        if not vectores:
            log(f"{nombre}: sin ningun rostro utilizable, no se carga")
            continue

        pendientes.append(insertar_persona(persona_id, nombre, suyos[0].origen, len(vectores)))
        for ruta, vector in vectores:
            pendientes.append(insertar_rostro(siguiente_rostro, persona_id, ruta, vector))
            siguiente_rostro += 1
        personas += 1
        rostros_cargados += len(vectores)
        log(f"{nombre}: {len(vectores)} rostros")

    for sentencia in pendientes:
        cliente.query(sentencia)

    return Informe(
        personas=personas,
        rostros=rostros_cargados,
        sin_rostro=tuple(sin_rostro),
    )


__all__ = [
    "Informe",
    "cargar",
    "crear_esquema",
    "esquema_existe",
    "insertar_persona",
    "insertar_rostro",
    "por_persona",
]
