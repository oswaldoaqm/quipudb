"""El esquema de Rikuq en QuipuDB (#302).

Dos tablas, que son los dos tipos de dato que exige el enunciado de la
Parte 5: ``personas`` guarda lo relacional (quien es) y ``rostros`` lo
multimedia (el vector de su cara). Van separadas porque una persona tiene
varias fotos: repetir su nombre en cada rostro seria guardar lo mismo 530
veces para George W. Bush.

El esquema no se escribe como texto suelto sino que se genera de la lista de
columnas de abajo. Asi el presupuesto de bytes se puede **calcular** y
comprobar en una prueba, en vez de quedar como un comentario que nadie
revisa cuando el esquema cambia.

Las reglas de tamano son las del motor (``engine/parser/semantic.py`` y
``engine/parser/bound_ast.py``):

- una pagina son 4096 bytes, menos 8 de cabecera y 1 de estado de slot, asi
  que un registro de un heap entra en **4087 bytes**;
- ``INT`` ocupa 4, ``VARCHAR(n)`` ocupa n, y ``VECTOR(d)`` ocupa d x 4,
  porque cada componente es un float de 32 bits.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.rostros.constantes import DIMENSIONES

PERSONAS = "personas"
ROSTROS = "rostros"

# engine/parser/semantic.py: 4096 - 8 (cabecera de pagina) - 1 (estado del slot)
MAX_BYTES_HEAP = 4087

BYTES_POR_COMPONENTE = 4
BYTES_INT = 4


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

    @property
    def bytes(self) -> int:
        if self.tipo == "VARCHAR":
            assert self.largo is not None
            return self.largo
        if self.tipo == "VECTOR":
            assert self.largo is not None
            return self.largo * BYTES_POR_COMPONENTE
        return BYTES_INT


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


def bytes_por_registro(tabla: str) -> int:
    """Lo que ocupa un registro de ``tabla``, con la regla del motor."""

    return sum(columna.bytes for columna in COLUMNAS[tabla])


def crear_tabla(tabla: str) -> str:
    cuerpo = ",\n  ".join(columna.sql for columna in COLUMNAS[tabla])
    return f"CREATE TABLE {tabla} (\n  {cuerpo}\n) USING HEAP"


# El indice que hace a esta aplicacion lo que es: buscar el rostro mas
# parecido sin recorrer las 4324 filas.
#
# Por que HNSW y no IVF: IVF necesita datos para entrenar sus centroides, asi
# que no se puede crear antes de cargar; HNSW se crea vacio y acepta las filas
# a medida que entran. Ademas el enunciado pide consultar en menos de 100 ms y
# HNSW responde en 0,69 ms sobre 100 000 vectores contra 11,92 ms de IVF
# (benchmarks de la Parte 4). El precio es construirlo mas lento y ocupar mas,
# que para 4324 rostros no se siente.
#
# Por que METRIC=cosine: los vectores de #305 salen normalizados, asi que el
# coseno y el producto punto dan lo mismo; el coseno es el que usa ArcFace y
# la literatura de reconocimiento facial, y el que hace comparables nuestros
# numeros con los publicados.
INDICES: tuple[str, ...] = (
    f"CREATE INDEX rostros_por_embedding ON {ROSTROS} (embedding) USING HNSW WITH METRIC=cosine",
    # Contar los rostros de una persona y traer los suyos sin recorrer la tabla.
    f"CREATE INDEX rostros_por_persona ON {ROSTROS} (persona_id) USING HASH",
    # Buscar a alguien por nombre desde la interfaz.
    f"CREATE INDEX personas_por_nombre ON {PERSONAS} (nombre) USING HASH",
)


def sentencias() -> tuple[str, ...]:
    """Las tablas y despues sus indices, en el orden en que hay que correrlas."""

    return (crear_tabla(PERSONAS), crear_tabla(ROSTROS), *INDICES)


__all__ = [
    "BYTES_INT",
    "BYTES_POR_COMPONENTE",
    "COLUMNAS",
    "INDICES",
    "MAX_BYTES_HEAP",
    "PERSONAS",
    "ROSTROS",
    "Columna",
    "bytes_por_registro",
    "crear_tabla",
    "sentencias",
]
