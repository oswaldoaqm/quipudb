"""Cargar el dataset de rostros en QuipuDB (#302).

Lee el manifiesto de #301, saca el vector de cada imagen con el pipeline de
#304 y #305, y lo inserta por el API REST del motor. No importa ``engine``:
todo va por HTTP.

Una imagen sin rostro no corta la carga. De 4324 fotos de prensa siempre hay
alguna donde YuNet no encuentra nada, y perder la carga entera por una sola
obligaria a empezar de cero. Se anota, se sigue, y al final se dice cuantas
entraron y cuantas no.

Uso, desde ``rikuq/server``::

    python -m app.carga.dataset --limite 200
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from app.carga.esquema import PERSONAS, ROSTROS, sentencias
from app.config import Settings
from app.dataset.lfw import DATOS, MANIFIESTO, Rostro, leer
from app.motor import ClienteMotor, MotorError, literal, literal_vector
from app.rostros.deteccion import crear_detector
from app.rostros.embedding import crear_descriptor, vectores_de
from app.rostros.errores import FeatureExtractionError

# Cuantos INSERT se mandan en una sola llamada. El API acepta un lote separado
# por ';'. Agrupar baja el costo de ida y vuelta; pasarse hace una peticion
# enorme que el motor tarda en responder y que, si falla, no dice cual fila
# tuvo la culpa.
LOTE = 50


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
    """El motor no acepta la lista de columnas: los valores van en el orden
    del esquema (``COLUMNAS[PERSONAS]``)."""

    return (
        f"INSERT INTO {PERSONAS} VALUES "
        f"({persona_id}, {literal(nombre)}, {literal(origen)}, {fotos})"
    )


def insertar_rostro(rostro_id: int, persona_id: int, ruta: str, vector: Iterable[float]) -> str:
    return (
        f"INSERT INTO {ROSTROS} VALUES "
        f"({rostro_id}, {persona_id}, {literal(ruta)}, {literal_vector(list(vector))})"
    )


def por_persona(rostros: Iterable[Rostro]) -> dict[str, list[Rostro]]:
    """Agrupa el manifiesto por persona, conservando el orden de aparicion."""

    agrupado: dict[str, list[Rostro]] = {}
    for rostro in rostros:
        agrupado.setdefault(rostro.persona, []).append(rostro)
    return agrupado


def _en_lotes(sentencias_sql: list[str], tamano: int = LOTE) -> Iterator[str]:
    for inicio in range(0, len(sentencias_sql), tamano):
        yield ";\n".join(sentencias_sql[inicio : inicio + tamano])


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

    for lote in _en_lotes(pendientes):
        cliente.query(lote)

    return Informe(
        personas=personas,
        rostros=rostros_cargados,
        sin_rostro=tuple(sin_rostro),
    )


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--manifiesto", type=Path, default=MANIFIESTO)
    parser.add_argument("--datos", type=Path, default=DATOS)
    parser.add_argument(
        "--limite",
        type=int,
        help="carga solo las primeras N imagenes del manifiesto, para probar",
    )
    parser.add_argument(
        "--rehacer",
        action="store_true",
        help="borra las tablas antes de crearlas",
    )
    args = parser.parse_args(argv)

    rostros = leer(args.manifiesto)
    if args.limite:
        rostros = rostros[: args.limite]

    with ClienteMotor.desde(Settings.del_entorno()) as cliente:
        try:
            if args.rehacer:
                # Solo las que estan: el motor rechaza un DROP de una tabla
                # que no existe, y `--rehacer` sobre una base limpia es lo
                # normal la primera vez.
                existentes = cliente.table_names()
                for tabla in (ROSTROS, PERSONAS):
                    if tabla in existentes:
                        cliente.query(f"DROP TABLE {tabla}")
            if not esquema_existe(cliente):
                crear_esquema(cliente)
            informe = cargar(cliente, rostros, datos=args.datos, log=print)
        except MotorError as error:
            print(f"error: {error}", file=sys.stderr)
            return 1

    print(informe.resumen())
    return 0


__all__ = [
    "LOTE",
    "Informe",
    "cargar",
    "crear_esquema",
    "esquema_existe",
    "insertar_persona",
    "insertar_rostro",
    "main",
    "por_persona",
]


if __name__ == "__main__":
    sys.exit(main())
