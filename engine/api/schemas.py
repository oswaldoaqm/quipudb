"""Modelos HTTP de la API, espejo de ``frontend/src/api/types.ts``.

Si el contrato cambia, este archivo y ese cambian en el mismo PR. Los nombres
de campo viajan en ``snake_case`` porque es lo que el frontend consume tal cual.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from engine.parser.ast import SqlTypeName

TableStructure = Literal["heap", "sequential", "bplus_clustered"]
IndexStructure = Literal["bplus_unclustered", "extendible_hash"]
ErrorKind = Literal["lex", "parse", "semantic", "unsupported"]


class ColumnInfo(BaseModel):
    """Una columna del esquema, como la dibuja el Panel de Archivos."""

    name: str
    type: SqlTypeName
    size: int | None = None
    """Solo lo declara VARCHAR. En el core vale 0 fuera de VARCHAR; aqui, None."""

    is_primary_key: bool = False
    """En el core la clave es una POSICION; aqui, un booleano por columna."""


class IndexInfo(BaseModel):
    """Un indice secundario de la tabla."""

    name: str
    column: str
    """El NOMBRE de la columna. En ``IndexMetadata`` es su posicion."""

    structure: IndexStructure
    supports_range: bool


class TableInfo(BaseModel):
    """Lo que ``GET /tables`` devuelve por cada tabla del catalogo.

    No es el espejo de ninguna estructura del motor: se compone del esquema del
    core, de la ``TableMetadata`` del planner y del tamano del archivo.
    """

    name: str
    storage: TableStructure
    columns: list[ColumnInfo]
    indexes: list[IndexInfo]
    record_count: int


class QueryRequest(BaseModel):
    """Cuerpo de ``POST /query``; ``sql`` puede contener un lote separado por ``;``."""

    sql: str


class QueryResponse(BaseModel):
    """Espejo de ``QueryResult``, con los tipos de columna que la interfaz pide."""

    columns: list[str]
    column_types: list[SqlTypeName]
    rows: list[list[object]]
    affected_rows: int = 0
    plan: dict[str, object] | None = None


class QueryErrorResponse(BaseModel):
    """Cuerpo de cualquier respuesta 400.

    Lineas y columnas se cuentan desde 1 y ``end_column`` es exclusivo, igual
    que ``Span``. Es la convencion de Monaco, asi que el rango llega al editor
    sin conversion. Un error sin ubicacion manda los cuatro campos en ``None``.
    """

    error: str
    kind: ErrorKind | None = None
    line: int | None = None
    column: int | None = Field(default=None)
    end_line: int | None = None
    end_column: int | None = None


__all__ = [
    "ColumnInfo",
    "ErrorKind",
    "IndexInfo",
    "IndexStructure",
    "QueryErrorResponse",
    "QueryRequest",
    "QueryResponse",
    "TableInfo",
    "TableStructure",
]
