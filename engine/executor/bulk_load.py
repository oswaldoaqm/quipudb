"""Carga masiva desde CSV: emparejar la cabecera y convertir cada campo.

Todo lo de este modulo es puro -- no toca el core --, asi que se prueba sin
compilar los bindings. La insercion en si la hace ``QueryProcessor.load_csv``,
que es quien tiene los locks y las transacciones.

Las reglas de conversion son las mismas que aplica el analizador semantico a
los literales de un INSERT: si un valor no se podria escribir en SQL para esa
columna, tampoco entra por CSV.
"""

from __future__ import annotations

import csv
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import TextIO

from engine.parser.ast import SqlTypeName
from engine.parser.bound_ast import BoundColumn, BoundSchema, BoundValue

INT32_MIN = -(2**31)
INT32_MAX = 2**31 - 1

MAX_REPORTED_ERRORS = 100
"""Cuantas filas fallidas se detallan en el reporte. Se cuentan todas; se
listan solo las primeras para que un CSV entero de basura no devuelva una
respuesta del tamano del archivo."""

_ENTERO = re.compile(r"[+-]?[0-9]+")
_FECHA = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_VERDADERO = frozenset({"true", "1"})
_FALSO = frozenset({"false", "0"})


class CsvLoadError(ValueError):
    """El archivo no se puede cargar: nada de el llego a insertarse.

    Cubre una cabecera que no calza con el esquema, un archivo vacio o una
    carga atomica que se deshizo. ``line`` es la linea del CSV (desde 1)
    cuando el problema tiene una.
    """

    def __init__(self, message: str, line: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.line = line


@dataclass(frozen=True, slots=True)
class RowError:
    """Una fila que no entro, con la linea del CSV donde termina."""

    line: int
    error: str


@dataclass(slots=True)
class LoadReport:
    """Lo que devuelve una carga parcial."""

    table: str
    inserted: int = 0
    failed: int = 0
    errors: list[RowError] = field(default_factory=list)

    @property
    def errors_truncated(self) -> bool:
        return self.failed > len(self.errors)

    def fail(self, line: int, error: str) -> None:
        self.failed += 1
        if len(self.errors) < MAX_REPORTED_ERRORS:
            self.errors.append(RowError(line, error))


DELIMITERS = (",", ";", "\t")
"""Separadores que se reconocen solos. El punto y coma es el que usa Excel
cuando la configuracion regional es de Peru (y de casi toda Latinoamerica),
porque ahi la coma es el separador decimal."""


@dataclass(frozen=True, slots=True)
class CsvHeader:
    """La cabecera ya leida: con que separador y en que linea estaba."""

    fields: tuple[str, ...]
    delimiter: str
    line: int


def read_header(stream: TextIO) -> CsvHeader:
    """Lee la cabecera saltando las lineas en blanco y detecta el separador.

    El separador se elige mirando solo la cabecera: el que la parte en mas
    columnas entre coma, punto y coma y tabulador; si empatan, la coma. Mirar
    las filas no hace falta y obligaria a leerlas dos veces.
    """

    line = 0
    while True:
        raw = stream.readline()
        if raw == "":
            raise CsvLoadError("el archivo esta vacio: falta la cabecera")
        line += 1
        if raw.strip():
            break

    def columnas(delimitador: str) -> int:
        # Sin `strict`: una cabecera con comillas no es valida para los otros
        # separadores, y eso no tiene que impedir compararlos.
        return len(next(csv.reader([raw], delimiter=delimitador)))

    mejor = max(DELIMITERS, key=columnas)
    try:
        fields = next(csv.reader([raw], delimiter=mejor, strict=True))
    except csv.Error as error:
        raise CsvLoadError(f"la cabecera esta mal formada: {error}", line=line) from None
    return CsvHeader(tuple(fields), mejor, line)


def match_header(header: Sequence[str], schema: BoundSchema, *, line: int = 1) -> tuple[int, ...]:
    """Devuelve, por cada columna del esquema, su posicion en el CSV.

    El orden del CSV es libre, pero tienen que estar todas las columnas y
    ninguna mas. Un nombre se busca primero exacto y, si no esta, sin
    distinguir mayusculas -- Excel suele capitalizar las cabeceras --, siempre
    que el esquema no tenga dos columnas que solo difieran en eso. Se ignoran
    los espacios de los bordes y las columnas sin nombre al final, que deja
    Excel cuando una fila termina en separador.
    """

    nombres = [nombre.strip() for nombre in header]
    while nombres and not nombres[-1]:
        nombres.pop()
    if not nombres:
        raise CsvLoadError("el CSV no tiene cabecera", line=line)
    if "" in nombres:
        posicion = nombres.index("") + 1
        raise CsvLoadError(f"la columna {posicion} de la cabecera no tiene nombre", line=line)

    esperadas = [columna.name for columna in schema.columns]
    por_minusculas: dict[str, list[str]] = {}
    for nombre in esperadas:
        por_minusculas.setdefault(nombre.casefold(), []).append(nombre)

    vistos: dict[str, int] = {}
    repetidas: list[str] = []
    sobran: list[str] = []
    for posicion, nombre in enumerate(nombres):
        if nombre not in esperadas:
            candidatas = por_minusculas.get(nombre.casefold(), [])
            if len(candidatas) != 1:
                sobran.append(nombre)
                continue
            nombre = candidatas[0]
        if nombre in vistos:
            repetidas.append(nombre)
        else:
            vistos[nombre] = posicion

    faltan = [nombre for nombre in esperadas if nombre not in vistos]
    problemas = []
    if faltan:
        problemas.append(f"faltan {_lista(faltan)}")
    if sobran:
        problemas.append(f"sobran {_lista(sobran)}")
    if repetidas:
        problemas.append(f"se repite {_lista(repetidas)}")
    if problemas:
        raise CsvLoadError(
            f"la cabecera no calza con la tabla {schema.table_name!r}: "
            + "; ".join(problemas)
            + f". La tabla tiene {_lista(esperadas)}",
            line=line,
        )
    return tuple(vistos[nombre] for nombre in esperadas)


def convert_row(
    fields: Sequence[str],
    mapping: Sequence[int],
    schema: BoundSchema,
    width: int,
    *,
    decimal_comma: bool = False,
) -> list[BoundValue]:
    """Convierte una fila del CSV al orden y los tipos del esquema.

    ``width`` es cuantas columnas tiene la cabecera, contando las vacias del
    final: una fila con mas o menos campos es un error aunque las que sobran
    o faltan no se usen, porque casi siempre significa una coma de mas dentro
    de un texto sin comillas. Las columnas sin nombre del final tienen que
    venir vacias: un valor ahi se perderia sin que nadie se entere.
    """

    if len(fields) != width:
        raise ValueError(f"tiene {len(fields)} campos y la cabecera tiene {width}")
    for posicion in range(len(mapping), width):
        if fields[posicion].strip():
            raise ValueError(
                f"el campo {posicion + 1} trae {fields[posicion]!r} y su columna de la "
                "cabecera no tiene nombre"
            )
    return [
        convert_field(fields[posicion], columna, decimal_comma=decimal_comma)
        for posicion, columna in zip(mapping, schema.columns, strict=True)
    ]


def convert_field(text: str, column: BoundColumn, *, decimal_comma: bool = False) -> BoundValue:
    """Convierte un campo al tipo declarado; lanza ``ValueError`` si no puede.

    El texto de un VARCHAR se guarda tal cual, espacios incluidos. En los
    demas tipos se ignoran los espacios de los bordes. No hay NULL en el
    motor, asi que un campo vacio solo es valido en un VARCHAR. Con
    ``decimal_comma`` -- un CSV separado por punto y coma -- un DOUBLE puede
    venir como ``15,5``.
    """

    tipo = column.data_type
    if tipo is SqlTypeName.VARCHAR:
        return _varchar(text, column)

    valor = text.strip()
    if not valor:
        raise ValueError(f"columna {column.name}: esta vacia y {tipo.value} no admite vacios")
    if tipo is SqlTypeName.INT:
        return _int(valor, column)
    if tipo is SqlTypeName.DOUBLE:
        if decimal_comma and "," in valor and "." not in valor:
            valor = valor.replace(",", ".", 1)
        return _double(valor, column)
    if tipo is SqlTypeName.BOOL:
        return _bool(valor, column)
    if tipo is SqlTypeName.DATE:
        return _date(valor, column)
    raise AssertionError(f"tipo SQL desconocido: {tipo!r}")


def _int(valor: str, column: BoundColumn) -> int:
    if not _ENTERO.fullmatch(valor):
        raise ValueError(f"columna {column.name}: {valor!r} no es un INT")
    numero = int(valor)
    if not INT32_MIN <= numero <= INT32_MAX:
        raise ValueError(f"columna {column.name}: {numero} esta fuera del rango INT de 32 bits")
    return numero


def _double(valor: str, column: BoundColumn) -> float:
    try:
        numero = float(valor)
    except ValueError:
        raise ValueError(f"columna {column.name}: {valor!r} no es un DOUBLE") from None
    # float() acepta "nan" e "inf", que el SQL no deja escribir.
    if not math.isfinite(numero):
        raise ValueError(f"columna {column.name}: DOUBLE requiere un valor finito")
    return numero


def _bool(valor: str, column: BoundColumn) -> bool:
    minusculas = valor.lower()
    if minusculas in _VERDADERO:
        return True
    if minusculas in _FALSO:
        return False
    raise ValueError(f"columna {column.name}: {valor!r} no es un BOOL (true/false o 1/0)")


def _date(valor: str, column: BoundColumn) -> date:
    # fromisoformat tambien acepta "20260924"; se exige la forma del SQL.
    if not _FECHA.fullmatch(valor):
        raise ValueError(f"columna {column.name}: {valor!r} no es una fecha AAAA-MM-DD")
    try:
        return date.fromisoformat(valor)
    except ValueError:
        raise ValueError(f"columna {column.name}: {valor!r} no es una fecha valida") from None


def _varchar(texto: str, column: BoundColumn) -> str:
    if "\0" in texto:
        raise ValueError(f"columna {column.name}: VARCHAR no admite bytes nulos")
    if column.length is None:
        raise ValueError(f"columna {column.name}: el esquema VARCHAR no tiene longitud")
    bytes_ = len(texto.encode("utf-8"))
    if bytes_ > column.length:
        raise ValueError(
            f"columna {column.name}: el texto ocupa {bytes_} bytes y supera "
            f"VARCHAR({column.length})"
        )
    return texto


def _lista(nombres: Sequence[str]) -> str:
    return ", ".join(repr(nombre) for nombre in nombres)


__all__ = [
    "DELIMITERS",
    "MAX_REPORTED_ERRORS",
    "CsvHeader",
    "CsvLoadError",
    "LoadReport",
    "RowError",
    "convert_field",
    "convert_row",
    "match_header",
    "read_header",
]
