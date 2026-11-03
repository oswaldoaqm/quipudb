"""Modelos HTTP de la API, espejo de ``frontend/src/api/types.ts``.

Si el contrato cambia, este archivo y ese cambian en el mismo PR. Los nombres
de campo viajan en ``snake_case`` porque es lo que el frontend consume tal cual.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from engine.parser.ast import DistanceMetric, SqlTypeName
from engine.parser.bound_ast import PointValue

TableStructure = Literal["heap", "sequential", "bplus_clustered"]
IndexStructure = Literal["bplus_unclustered", "extendible_hash", "rtree", "inverted_index"]
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


class RadiusContextResponse(BaseModel):
    """Contexto aditivo de radio. No depende del texto descriptivo del plan."""

    kind: Literal["radius"] = "radius"
    table: str | None = None
    column: str
    center: PointValue
    radius: float
    metric: DistanceMetric
    unit: Literal["meters", "degrees"]
    operator: Literal["<", "<="]


class PolygonContextResponse(BaseModel):
    """Contexto aditivo de ``DENTRO``: los vertices en el orden del SQL, sin cerrar."""

    kind: Literal["polygon"] = "polygon"
    table: str | None = None
    column: str
    vertices: list[PointValue]


SpatialContextResponse = RadiusContextResponse | PolygonContextResponse


class QueryResponse(BaseModel):
    """Espejo de ``QueryResult``, con los tipos de columna que la interfaz pide."""

    columns: list[str]
    column_types: list[SqlTypeName]
    rows: list[list[object]]
    affected_rows: int = 0
    plan: dict[str, object] | None = None
    spatial_context: SpatialContextResponse | None = Field(default=None, discriminator="kind")
    is_explain: bool = False


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


class LoadRowError(BaseModel):
    """Una fila del CSV que no entro."""

    line: int
    """Linea del archivo donde empieza la fila, contando la cabecera como 1."""

    error: str


class LoadResponse(BaseModel):
    """Cuerpo de ``POST /tables/{tabla}/load``.

    Todavia no lo consume el frontend, asi que no tiene espejo en
    ``types.ts``. ``failed`` cuenta todas las filas que no entraron; ``errors``
    detalla solo las primeras, y ``errors_truncated`` avisa si hay mas.
    """

    table: str
    encoding: Literal["utf-8", "cp1252"]
    """Con que se leyo el archivo. cp1252 es lo que escribe Excel en Windows."""

    inserted: int
    failed: int
    errors: list[LoadRowError]
    errors_truncated: bool


class MediaColumnsRequest(BaseModel):
    """Nombres de las cuatro columnas; el orden fisico es libre."""

    id: str = "id"
    name: str = "nombre"
    path: str = "ruta"
    vector: str = "rasgos"


class MediaLoadRequest(BaseModel):
    """Carpeta del servidor y vocabulario ya entrenado; no rutas del navegador."""

    folder: str = Field(min_length=1, max_length=4096)
    vocabulary: str = Field(min_length=1, max_length=4096)
    columns: MediaColumnsRequest = Field(default_factory=MediaColumnsRequest)
    start_id: int = Field(default=1, ge=0, le=2**31 - 1, strict=True)
    batch_size: int = Field(default=32, ge=1, le=256, strict=True)
    recursive: bool = False
    max_file_bytes: int = Field(default=32 * 1024**2, ge=1, le=64 * 1024**2, strict=True)
    max_keypoints: int = Field(default=500, ge=1, le=5000, strict=True)
    max_audio_seconds: float = Field(default=30.0, gt=0, le=300, allow_inf_nan=False)


class MediaFileErrorResponse(BaseModel):
    path: str
    error: str


class MediaJobResponse(BaseModel):
    """Progreso consultable sin tomar el lock del motor ni devolver vectores."""

    job_id: str
    table: str
    status: Literal["pending", "running", "completed", "failed", "cancelled"]
    discovered: int
    processed: int
    inserted: int
    failed: int
    next_id: int
    errors: list[MediaFileErrorResponse]
    errors_truncated: bool
    elapsed_ms: float
    codebook_id: str | None = None
    dimension: int | None = None
    error: str | None = None


__all__ = [
    "ColumnInfo",
    "ErrorKind",
    "IndexInfo",
    "IndexStructure",
    "LoadResponse",
    "LoadRowError",
    "MediaColumnsRequest",
    "MediaFileErrorResponse",
    "MediaJobResponse",
    "MediaLoadRequest",
    "QueryErrorResponse",
    "QueryRequest",
    "QueryResponse",
    "TableInfo",
    "TableStructure",
]
