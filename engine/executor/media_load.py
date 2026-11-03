"""Contrato de filas multimedia y validacion pura de su tabla de destino."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from engine.executor.bulk_load import INT32_MAX, INT32_MIN, MAX_REPORTED_ERRORS
from engine.parser.ast import SqlTypeName
from engine.parser.bound_ast import BoundSchema


class MediaLoadError(ValueError):
    """La configuracion de una carga multimedia no es valida."""


@dataclass(frozen=True, slots=True)
class MediaColumns:
    id: str = "id"
    name: str = "nombre"
    path: str = "ruta"
    vector: str = "rasgos"

    @property
    def names(self) -> tuple[str, ...]:
        return self.id, self.name, self.path, self.vector


@dataclass(frozen=True, slots=True)
class MediaRow:
    id: int
    name: str
    path: str
    vector: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class MediaFileError:
    path: str
    error: str


@dataclass(slots=True)
class MediaLoadReport:
    table: str
    inserted: int = 0
    failed: int = 0
    errors: list[MediaFileError] = field(default_factory=list)

    @property
    def errors_truncated(self) -> bool:
        return self.failed > len(self.errors)

    def fail(self, path: str, error: str) -> None:
        self.failed += 1
        if len(self.errors) < MAX_REPORTED_ERRORS:
            self.errors.append(MediaFileError(path, error[:1024]))

    def merge(self, other: MediaLoadReport) -> None:
        self.inserted += other.inserted
        self.failed += other.failed
        remaining = MAX_REPORTED_ERRORS - len(self.errors)
        self.errors.extend(other.errors[:remaining])


def validate_media_schema(
    schema: BoundSchema, columns: MediaColumns, dimension: int | None = None
) -> None:
    """No inventa valores para columnas adicionales ni usa la posicion fisica."""

    expected = columns.names
    actual = {column.name: column for column in schema.columns}
    if len(set(expected)) != 4 or set(actual) != set(expected):
        raise MediaLoadError(
            "la tabla multimedia necesita exactamente cuatro columnas distintas: "
            + ", ".join(expected)
        )
    if (
        actual[columns.id].data_type is not SqlTypeName.INT
        or schema.columns[schema.key_column].name != columns.id
    ):
        raise MediaLoadError(f"la columna {columns.id!r} debe ser INT PRIMARY KEY")
    for name in (columns.name, columns.path):
        if actual[name].data_type is not SqlTypeName.VARCHAR:
            raise MediaLoadError(f"la columna {name!r} debe ser VARCHAR")
    vector = actual[columns.vector]
    if vector.data_type is not SqlTypeName.VECTOR:
        raise MediaLoadError(f"la columna {columns.vector!r} debe ser VECTOR(dim)")
    if dimension is not None and vector.length != dimension:
        raise MediaLoadError(
            f"VECTOR({vector.length}) no coincide con las {dimension} palabras del vocabulario"
        )


def media_values(row: MediaRow, schema: BoundSchema, columns: MediaColumns) -> list[object]:
    """Valida cada archivo antes de escribir, incluidas longitudes en bytes UTF-8."""

    if isinstance(row.id, bool) or not isinstance(row.id, int) or not INT32_MIN <= row.id <= INT32_MAX:
        raise ValueError("el identificador no cabe en un INT de 32 bits")
    values: dict[str, object] = {
        columns.id: row.id,
        columns.name: row.name,
        columns.path: row.path,
        columns.vector: row.vector,
    }
    for column in schema.columns:
        value = values[column.name]
        if column.data_type is SqlTypeName.VARCHAR and (
            not isinstance(value, str) or len(value.encode("utf-8")) > column.length
        ):
            raise ValueError(f"el valor de {column.name!r} excede VARCHAR({column.length})")
        if column.data_type is SqlTypeName.VECTOR and (
            len(row.vector) != column.length or any(
                not math.isfinite(v) or abs(v) > 3.4028234663852886e38 for v in row.vector
            )
        ):
            raise ValueError(f"se esperaba un VECTOR({column.length}) finito de float32")
    return [values[column.name] for column in schema.columns]
