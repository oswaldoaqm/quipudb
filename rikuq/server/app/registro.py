"""Registrar fotos en la base desde la interfaz, listarlas y eliminarlas (#342).

Hasta aqui la base solo se llenaba con ``python -m app.carga.dataset``. Esto
deja hacerlo en vivo: una foto con un solo rostro, el nombre de la persona y
el permiso de quien la sube. Todo va por el API REST del motor, como el resto
de Rikuq.

El motor no tiene ``UPDATE`` ni ``MAX`` sin ``GROUP BY``. Por eso:

- el siguiente id es el mayor mas uno, con ``ORDER BY id DESC LIMIT 1``;
- el conteo ``personas.fotos`` se reescribe borrando la fila e insertandola
  otra vez con el numero nuevo.

Solo se listan y se eliminan las fotos subidas asi, que se reconocen por su
ruta (``subidas/...``): las del dataset se cargan y se rehacen con su comando,
y no se tocan desde la interfaz.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from app.carga.esquema import PERSONAS, ROSTROS, sentencias
from app.motor import ClienteMotor, literal, literal_vector
from app.pipeline import ImagenEntrada

CARPETA = "subidas"
"""Donde quedan las fotos registradas, dentro de ``rikuq/datos``."""

ORIGEN = "subida"
"""``personas.origen`` de quien entra por la interfaz (el dataset usa lfw y equipo)."""

MAX_NOMBRE = 50
"""Caracteres. ``personas.nombre`` es VARCHAR(100) en bytes, y una letra con
tilde ocupa dos en UTF-8: 50 caracteres caben siempre."""

_PALABRA = r"[^\W\d_]+(?:['.-][^\W\d_]+)*\.?"
NOMBRE_VALIDO = re.compile(rf"{_PALABRA}(?:_{_PALABRA})*")
"""Palabras de letras unidas por ``_``, como las carpetas de LFW
(``Ann_Veneman``). Se admiten tildes, ñ, apostrofo, guion y punto."""


class ErrorRegistro(Exception):
    """Algo que el usuario puede corregir, con su codigo HTTP y su motivo."""

    def __init__(self, status: int, codigo: str, mensaje: str) -> None:
        super().__init__(mensaje)
        self.status = status
        self.codigo = codigo
        self.mensaje = mensaje


@dataclass(frozen=True, slots=True)
class Persona:
    id: int
    nombre: str
    origen: str
    fotos: int


def normalizar_nombre(texto: str) -> str:
    """``"  ana  maria  Quispe "`` → ``"ana_maria_Quispe"``, o ``ErrorRegistro``.

    Los espacios pasan a ``_`` para que "Ann Veneman" sea la misma persona que
    la carpeta ``Ann_Veneman`` del dataset. No se cambian mayusculas: el
    nombre se guarda como se escribio.
    """

    nombre = "_".join(texto.replace("_", " ").split())
    if not nombre:
        raise ErrorRegistro(422, "nombre_invalido", "escribe el nombre de la persona")
    if len(nombre) > MAX_NOMBRE:
        raise ErrorRegistro(
            422, "nombre_invalido", f"el nombre tiene mas de {MAX_NOMBRE} caracteres"
        )
    if not NOMBRE_VALIDO.fullmatch(nombre):
        raise ErrorRegistro(
            422,
            "nombre_invalido",
            "el nombre solo puede tener letras, espacios, apostrofos, guiones y puntos",
        )
    return nombre


def extension_de(datos: bytes) -> str:
    """La extension segun los primeros bytes, no segun lo que diga el cliente."""

    if datos.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if datos.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if datos[:4] == b"RIFF" and datos[8:12] == b"WEBP":
        return ".webp"
    raise ErrorRegistro(415, "imagen_invalida", "usa una imagen JPEG, PNG o WebP")


def vector_unico(imagen: ImagenEntrada) -> tuple[float, ...]:
    """El vector del unico rostro, si sirve como referencia.

    Con dos caras no se sabe cual es la persona, y un rostro chico se detecta
    bien pero se describe mal: guardarlo empeoraria cada consulta posterior.
    """

    if len(imagen.rostros) != 1:
        raise ErrorRegistro(
            422,
            "varios_rostros",
            f"la foto tiene {len(imagen.rostros)} rostros; usa una con una sola persona",
        )
    rostro = imagen.rostros[0]
    if not rostro.fiable:
        raise ErrorRegistro(
            422,
            "rostro_pequeno",
            "el rostro es muy pequeno para registrarlo; usa una foto mas cercana",
        )
    return rostro.vector


def asegurar_esquema(cliente: ClienteMotor) -> None:
    """Crea las tablas e indices si la base esta vacia (sin cargar el dataset)."""

    if {PERSONAS, ROSTROS} <= cliente.table_names():
        return
    for sentencia in sentencias():
        cliente.query(sentencia)


def siguiente_id(cliente: ClienteMotor, tabla: str) -> int:
    filas = cliente.query(f"SELECT id FROM {tabla} ORDER BY id DESC LIMIT 1").rows
    return int(filas[0][0]) + 1 if filas else 1


def persona_por_nombre(cliente: ClienteMotor, nombre: str) -> Persona | None:
    """La persona con ese nombre, sin distinguir mayusculas.

    Primero la busqueda exacta, que usa el indice hash de ``nombre``. Si no
    esta, se recorre la tabla: el motor no tiene LOWER y "colin powell" tiene
    que sumar a ``Colin_Powell``, no crear a otra persona. Son pocas filas.
    """

    columnas = f"SELECT id, nombre, origen, fotos FROM {PERSONAS}"
    filas = cliente.query(f"{columnas} WHERE nombre = {literal(nombre)}").rows
    if filas:
        return Persona(*filas[0])
    buscado = nombre.casefold()
    for fila in cliente.query(columnas).rows:
        if str(fila[1]).casefold() == buscado:
            return Persona(*fila)
    return None


def persona_por_id(cliente: ClienteMotor, persona_id: int) -> Persona | None:
    filas = cliente.query(
        f"SELECT id, nombre, origen, fotos FROM {PERSONAS} WHERE id = {int(persona_id)}"
    ).rows
    return Persona(*filas[0]) if filas else None


def _insertar_persona(persona: Persona) -> str:
    return (
        f"INSERT INTO {PERSONAS} VALUES ({persona.id}, {literal(persona.nombre)}, "
        f"{literal(persona.origen)}, {persona.fotos})"
    )


def _reescribir_persona(persona: Persona) -> list[str]:
    """``UPDATE personas SET fotos = ...`` sin UPDATE."""

    return [f"DELETE FROM {PERSONAS} WHERE id = {persona.id}", _insertar_persona(persona)]


def registrar(
    cliente: ClienteMotor, nombre: str, datos: bytes, vector: tuple[float, ...], raiz: Path
) -> dict:
    """Guarda la foto y la inserta en la base. Devuelve lo que quedo registrado."""

    extension = extension_de(datos)
    asegurar_esquema(cliente)
    existente = persona_por_nombre(cliente, nombre)
    rostro_id = siguiente_id(cliente, ROSTROS)
    if existente:
        persona = Persona(existente.id, existente.nombre, existente.origen, existente.fotos + 1)
        sentencias_persona = _reescribir_persona(persona)
    else:
        persona = Persona(siguiente_id(cliente, PERSONAS), nombre, ORIGEN, 1)
        sentencias_persona = [_insertar_persona(persona)]

    # El id va en el nombre para encontrar el archivo a ojo; el sufijo evita
    # pisar uno que haya quedado de un intento anterior.
    ruta = f"{CARPETA}/{rostro_id}-{uuid4().hex[:8]}{extension}"
    archivo = raiz / ruta
    archivo.parent.mkdir(parents=True, exist_ok=True)
    archivo.write_bytes(datos)
    try:
        # El rostro primero: si algo falla despues, la persona sigue igual que
        # antes y la foto ya se puede identificar.
        cliente.query(
            ";\n".join(
                [
                    f"INSERT INTO {ROSTROS} VALUES ({rostro_id}, {persona.id}, "
                    f"{literal(ruta)}, {literal_vector(vector)})",
                    *sentencias_persona,
                ]
            )
        )
    except Exception:
        archivo.unlink(missing_ok=True)
        raise
    return {
        "id": rostro_id,
        "persona_id": persona.id,
        "persona": persona.nombre,
        "persona_nueva": existente is None,
        "fotos": persona.fotos,
        "foto": f"/rostros/{rostro_id}/foto",
    }


def subidos(cliente: ClienteMotor) -> list[dict]:
    """Las fotos registradas desde la interfaz, de la mas nueva a la mas vieja.

    El motor no tiene LIKE: se piden ``id, persona_id, ruta`` (sin el vector)
    y se filtra la ruta aqui. Son unos pocos miles de filas cortas.
    """

    if {PERSONAS, ROSTROS} - cliente.table_names():
        return []
    filas = [
        (int(rostro_id), int(persona_id), str(ruta))
        for rostro_id, persona_id, ruta in cliente.query(
            f"SELECT id, persona_id, ruta FROM {ROSTROS}"
        ).rows
        if str(ruta).startswith(f"{CARPETA}/")
    ]
    nombres: dict[int, str | None] = {}
    for _, persona_id, _ in filas:
        if persona_id not in nombres:
            persona = persona_por_id(cliente, persona_id)
            nombres[persona_id] = persona.nombre if persona else None
    return [
        {
            "id": rostro_id,
            "persona_id": persona_id,
            "persona": nombres[persona_id],
            "foto": f"/rostros/{rostro_id}/foto",
        }
        for rostro_id, persona_id, _ in sorted(filas, reverse=True)
    ]


def eliminar(cliente: ClienteMotor, rostro_id: int, raiz: Path) -> dict:
    """Borra una foto subida, su archivo y la persona si se queda sin fotos."""

    filas = cliente.query(
        f"SELECT persona_id, ruta FROM {ROSTROS} WHERE id = {int(rostro_id)}"
    ).rows
    if not filas:
        raise ErrorRegistro(404, "no_existe", "esa foto ya no esta en la base")
    persona_id, ruta = int(filas[0][0]), str(filas[0][1])
    if not ruta.startswith(f"{CARPETA}/"):
        raise ErrorRegistro(
            403,
            "no_permitido",
            "solo se pueden eliminar las fotos registradas desde Rikuq",
        )

    persona = persona_por_id(cliente, persona_id)
    restantes = (
        len(cliente.query(f"SELECT id FROM {ROSTROS} WHERE persona_id = {persona_id}").rows) - 1
    )
    sentencias_sql = [f"DELETE FROM {ROSTROS} WHERE id = {int(rostro_id)}"]
    if persona and restantes <= 0:
        sentencias_sql.append(f"DELETE FROM {PERSONAS} WHERE id = {persona_id}")
    elif persona:
        sentencias_sql += _reescribir_persona(
            Persona(persona.id, persona.nombre, persona.origen, restantes)
        )
    cliente.query(";\n".join(sentencias_sql))

    archivo = (raiz / ruta).resolve()
    if archivo.is_relative_to((raiz / CARPETA).resolve()):
        archivo.unlink(missing_ok=True)
    return {
        "id": int(rostro_id),
        "persona_id": persona_id,
        "persona": persona.nombre if persona else None,
        "persona_eliminada": bool(persona) and restantes <= 0,
    }


__all__ = [
    "CARPETA",
    "MAX_NOMBRE",
    "ORIGEN",
    "ErrorRegistro",
    "Persona",
    "eliminar",
    "extension_de",
    "normalizar_nombre",
    "registrar",
    "subidos",
    "vector_unico",
]
