"""Composicion pura de lo que la API devuelve.

Aqui no hay HTTP. Son las traducciones que el frontend no deberia hacer --
mapear posiciones de columna a nombres obligaria a la interfaz a conocer la
representacion interna del catalogo -- y se aislan asi para poder probarlas sin
levantar el servidor ni compilar los bindings.
"""

from __future__ import annotations

from typing import Any

from engine.api.schemas import (
    ColumnInfo,
    ErrorKind,
    IndexInfo,
    LoadResponse,
    LoadRowError,
    QueryErrorResponse,
    QueryResponse,
    TableInfo,
)
from engine.executor.bulk_load import LoadReport
from engine.executor.native import from_native_schema
from engine.executor.result import QueryResult
from engine.parser.bound_ast import BoundSchema
from engine.parser.errors import (
    SQLError,
    SQLLexError,
    SQLParseError,
    SQLSemanticError,
    SQLUnsupportedError,
)
from engine.planner.native_catalog import load_table_metadata
from engine.planner.optimizer import TableMetadata

# Las cuatro derivan directamente de SQLError y no entre si, asi que el orden
# de esta tabla no decide nada; se mantiene por legibilidad.
_KINDS: tuple[tuple[type[SQLError], ErrorKind], ...] = (
    (SQLLexError, "lex"),
    (SQLParseError, "parse"),
    (SQLSemanticError, "semantic"),
    (SQLUnsupportedError, "unsupported"),
)


def describe_table(schema: BoundSchema, metadata: TableMetadata) -> TableInfo:
    """Arma un ``TableInfo`` a partir del esquema y la metadata del planner.

    Las cinco traducciones del contrato viven aqui: la posicion de la columna
    de un indice pasa a su nombre, ``key_column`` pasa a un booleano por
    columna, y el tamano del archivo pasa a ``record_count``. Las otras dos
    --``Column.length`` a ``None`` fuera de VARCHAR, y el tipo de cada
    columna-- ya las hacen ``from_native_schema`` y el ejecutor.
    """

    columnas = [
        ColumnInfo(
            name=columna.name,
            type=columna.data_type,
            size=columna.length,
            is_primary_key=(posicion == schema.key_column),
        )
        for posicion, columna in enumerate(schema.columns)
    ]

    indices = []
    for indice in metadata.indexes:
        if not 0 <= indice.column < len(schema.columns):
            raise ValueError(
                f"el indice {indice.name!r} de {metadata.name!r} apunta a la columna "
                f"inexistente {indice.column}"
            )
        indices.append(
            IndexInfo(
                name=indice.name,
                column=schema.columns[indice.column].name,
                structure=indice.structure.value,
                supports_range=indice.supports_range,
            )
        )

    return TableInfo(
        name=metadata.name,
        storage=metadata.structure.value,
        columns=columnas,
        indexes=indices,
        record_count=metadata.rows or 0,
    )


def describe_catalog(database: Any, native: Any) -> list[TableInfo]:
    """Describe todas las tablas registradas, en orden alfabetico estable."""

    return [
        describe_table(
            from_native_schema(database.table_info(nombre).schema, native),
            load_table_metadata(database, nombre),
        )
        for nombre in sorted(str(nombre) for nombre in database.table_names())
    ]


def to_response(result: QueryResult) -> QueryResponse:
    """Convierte el resultado del ejecutor al cuerpo de ``POST /query``.

    Las filas viajan como listas, no como objetos: su orden es el de
    ``columns``. Una sentencia sin filas deja las tres primeras vacias, informa
    en ``affected_rows`` y manda ``plan: null``.
    """

    return QueryResponse(
        columns=list(result.columns),
        column_types=list(result.column_types),
        rows=[list(fila) for fila in result.rows],
        affected_rows=result.affected_rows,
        plan=result.plan.to_dict() if result.plan is not None else None,
    )


def to_error(error: SQLError) -> QueryErrorResponse:
    """Traduce un error del motor al cuerpo de un 400.

    El mensaje viaja **sin reformular**: el frontend ya muestra esos textos con
    datos falsos y no deben cambiar al conectarse al motor real.
    """

    span = getattr(error, "span", None)
    return QueryErrorResponse(
        error=error.message,
        kind=_kind_of(error),
        line=getattr(span, "line", None),
        column=getattr(span, "column", None),
        end_line=getattr(span, "end_line", None),
        end_column=getattr(span, "end_column", None),
    )


def to_load_response(report: LoadReport, encoding: str = "utf-8") -> LoadResponse:
    """Convierte el reporte de una carga parcial al cuerpo de la respuesta."""

    return LoadResponse(
        table=report.table,
        encoding=encoding,
        inserted=report.inserted,
        failed=report.failed,
        errors=[LoadRowError(line=e.line, error=e.error) for e in report.errors],
        errors_truncated=report.errors_truncated,
    )


def _kind_of(error: SQLError) -> ErrorKind | None:
    for clase, nombre in _KINDS:
        if isinstance(error, clase):
            return nombre
    return None


__all__ = [
    "describe_catalog",
    "describe_table",
    "to_error",
    "to_load_response",
    "to_response",
]
