"""El esquema de Rikuq en QuipuDB (#302).

Dos tablas, que son los dos tipos de dato que exige el enunciado de la
Parte 5: ``personas`` guarda lo relacional (quien es) y ``rostros`` lo
multimedia (el vector de su cara). Van separadas porque una persona tiene
varias fotos: repetir su nombre en cada rostro seria guardar lo mismo 530
veces para George W. Bush.

El esquema no se escribe como texto suelto sino que se genera de la lista de
columnas de abajo, para poder comprobarlo en una prueba.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.rostros.embedding import DIMENSIONES

PERSONAS = "personas"
ROSTROS = "rostros"


@dataclass(frozen=True, slots=True)
class Columna:
    nombre: str
    tipo: str
    largo: int | None = None
    clave: bool = False

    @property
    def sql(self) -> str:
        tipo = self.tipo if self.largo is None else f"{self.tipo}({self.largo})"
        return f"{self.nombre} {tipo}" + (" PRIMARY KEY" if self.clave else "")


COLUMNAS: dict[str, tuple[Columna, ...]] = {
    PERSONAS: (
        Columna("id", "INT", clave=True),
        # El nombre mas largo de LFW ronda los 35 caracteres; 100 deja sitio
        # para nombres del equipo con apellidos compuestos.
        Columna("nombre", "VARCHAR", 100),
        Columna("origen", "VARCHAR", 10),  # lfw | equipo
        Columna("fotos", "INT"),
    ),
    ROSTROS: (
        Columna("id", "INT", clave=True),
        Columna("persona_id", "INT"),
        # La ruta relativa del manifiesto, p. ej.
        # "lfw/Abdullah_Gul/Abdullah_Gul_0001.jpg": 40 caracteres largos.
        Columna("ruta", "VARCHAR", 200),
        Columna("embedding", "VECTOR", DIMENSIONES),
    ),
}


def crear_tabla(tabla: str) -> str:
    cuerpo = ",\n  ".join(columna.sql for columna in COLUMNAS[tabla])
    return f"CREATE TABLE {tabla} (\n  {cuerpo}\n) USING HEAP"


__all__ = ["COLUMNAS", "PERSONAS", "ROSTROS", "Columna", "crear_tabla"]
