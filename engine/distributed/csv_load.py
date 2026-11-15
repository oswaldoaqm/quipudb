"""Carga de un CSV a través del coordinador (#292).

El nodo v1 no tiene una ruta de carga, y agregarla cambiaría el contrato. El
coordinador lee el CSV con las mismas funciones que la API local
(``engine/executor/bulk_load.py``): misma detección del separador, mismo
emparejamiento de la cabecera y mismas conversiones por tipo. Cada fila
convertida se manda como un ``INSERT`` al fragmento dueño de su clave, por la
ruta ``execute`` de siempre. Es una petición HTTP por fila: más lento que una
carga local, pero no toca los nodos ni el contrato.

Cada fila es su propia unidad, como en la carga local por omisión: las que no
se pueden convertir o insertar se reportan con su línea y las demás quedan.
La carga atómica se rechaza antes de leer nada, porque deshacer filas ya
insertadas en dos nodos no se puede garantizar sin transacciones distribuidas.

Si un nodo deja de responder durante la carga, sus filas siguientes fallan sin
volver a esperarlo, y las del otro nodo siguen entrando. El informe dice
cuántas entraron.
"""

from __future__ import annotations

import csv
from datetime import date
from typing import TextIO

from engine.distributed.client import NodeFailure
from engine.distributed.contracts import DistributedTable, Fragment
from engine.distributed.partitioning import owner_for_key
from engine.distributed.router import double_sql
from engine.executor.bulk_load import (
    CsvLoadError,
    LoadReport,
    convert_row,
    match_header,
    read_header,
)

UNREACHABLE = frozenset({"NODE_UNAVAILABLE", "NODE_TIMEOUT"})
"""Con estos códigos no vale la pena volver a llamar al nodo en esta carga."""


def value_sql(value: object) -> str:
    """Un valor ya convertido por ``convert_field``, como literal SQL."""

    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return double_sql(value)
    if isinstance(value, date):
        return f"DATE '{value.isoformat()}'"
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    raise TypeError(f"el CSV no carga valores {type(value).__name__}")


def insert_sql(table: str, row: list[object]) -> str:
    return f"INSERT INTO {table} VALUES ({', '.join(value_sql(v) for v in row)})"


def reject_atomic() -> CsvLoadError:
    return CsvLoadError(
        "[UNSUPPORTED_DISTRIBUTED] la carga atómica no se admite por el coordinador: "
        "no se puede deshacer en varios nodos sin transacciones distribuidas. "
        "Carga sin atomic y revisa el informe de filas"
    )


async def load_csv(coordinator, table: DistributedTable, stream: TextIO) -> LoadReport:
    """Reparte las filas de ``stream`` entre los fragmentos de ``table``."""

    schema = table.bound_schema
    try:
        header = read_header(stream)
    except UnicodeDecodeError as error:
        raise CsvLoadError(f"no se pudo leer la cabecera: {error}", line=1) from None
    mapping = match_header(header.fields, schema, line=header.line)
    reader = csv.reader(stream, delimiter=header.delimiter, strict=True)
    report = LoadReport(table.name)
    down: dict[str, int] = {}

    while True:
        line = header.line + reader.line_num + 1
        try:
            fields = next(reader)
        except StopIteration:
            break
        except csv.Error as error:
            report.fail(line, f"CSV mal formado: {error}")
            continue
        except UnicodeDecodeError:
            report.fail(line, "el archivo deja de ser UTF-8 valido; el resto no se leyo")
            break
        if not fields:
            continue  # linea en blanco
        try:
            row = convert_row(
                fields,
                mapping,
                schema,
                len(header.fields),
                decimal_comma=header.delimiter == ";",
            )
            fragment = _owner(table, row[schema.key_column])
        except ValueError as error:
            report.fail(line, str(error))
            continue
        if fragment.node_id in down:
            report.fail(
                line,
                f"el nodo {fragment.node_id} dejó de responder en la línea "
                f"{down[fragment.node_id]}; no se envió",
            )
            continue
        try:
            await coordinator.client(fragment.node_id).execute(
                fragment.fragment_id, table.name, insert_sql(table.name, row), write=True
            )
        except NodeFailure as failure:
            if failure.code in UNREACHABLE:
                down[fragment.node_id] = line
            report.fail(line, failure.public_message())
            continue
        report.inserted += 1
    return report


def _owner(table: DistributedTable, key: object) -> Fragment:
    if table.mode == "single":
        return table.fragments[0]
    return owner_for_key(table, key)


__all__ = ["insert_sql", "load_csv", "reject_atomic", "value_sql"]
