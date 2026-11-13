"""Modelos inmutables del catálogo JSON definido en el issue #275."""

from __future__ import annotations

import itertools
from typing import Annotated, Literal, Self
from urllib.parse import urlsplit

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator, model_validator

from engine.parser.ast import (
    ColumnDefinition,
    CreateTableStatement,
    SqlType,
    SqlTypeName,
    StorageKind,
)
from engine.parser.ast import (
    Identifier as SqlIdentifier,
)
from engine.parser.bound_ast import BoundColumn, BoundSchema
from engine.parser.errors import SQLError
from engine.parser.semantic import bind_create_table
from engine.parser.span import Span

INT32_MIN = -(2**31)
INT32_MAX = 2**31 - 1
PROTOCOL_VERSION = 1

Int32 = Annotated[int, Field(strict=True, ge=INT32_MIN, le=INT32_MAX)]
PositiveInt = Annotated[int, Field(strict=True, gt=0)]
Identifier = Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")]
EntityId = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
ColumnType = Literal["INT", "DOUBLE", "BOOL", "DATE", "VARCHAR", "POINT", "VECTOR"]
Storage = Literal["heap", "sequential", "bplus_clustered"]
IndexStructure = Literal[
    "bplus_unclustered",
    "extendible_hash",
    "rtree",
    "inverted_index",
    "ivf",
    "hnsw",
]


def _array(value: object) -> tuple:
    if not isinstance(value, (list, tuple)):
        # Pydantic v2 no convierte TypeError de validadores en ValidationError.
        raise ValueError("se requiere un array")  # noqa: TRY004
    return tuple(value)


class ContractModel(BaseModel):
    model_config = ConfigDict(
        strict=True,
        extra="forbid",
        frozen=True,
        revalidate_instances="always",
    )


class Node(ContractModel):
    node_id: EntityId
    base_url: str

    @field_validator("base_url")
    @classmethod
    def valid_url(cls, value: str) -> str:
        try:
            url = urlsplit(value)
            port = url.port
            valid = (
                url.scheme in {"http", "https"}
                and url.hostname
                and not url.path
                and not url.query
                and not url.fragment
                and url.username is None
                and url.password is None
                and not any(c.isspace() for c in value)
                and not any(c in value for c in "?#\\")
                and (port is None or port > 0)
                and not value.endswith(":")
            )
        except ValueError as error:
            raise ValueError("base_url inválida") from error
        if not valid:
            raise ValueError("base_url requiere HTTP(S), host y ningún path ni barra final")
        return value


class Fragment(ContractModel):
    fragment_id: EntityId
    node_id: EntityId
    lower: Int32 | None
    upper: Int32 | None

    @model_validator(mode="after")
    def nonempty(self) -> Self:
        if self.lower is not None and self.upper is not None and self.lower >= self.upper:
            raise ValueError("rango vacío o invertido: lower debe ser menor que upper")
        if self.upper == INT32_MIN:
            raise ValueError("rango vacío dentro del dominio INT32")
        return self


class Column(ContractModel):
    name: Identifier
    type: ColumnType
    size: PositiveInt | None

    @model_validator(mode="after")
    def valid_size(self) -> Self:
        if (self.type in {"VARCHAR", "VECTOR"}) != (self.size is not None):
            raise ValueError("size debe ser positivo para VARCHAR/VECTOR y null para otros tipos")
        return self


class DeclaredIndex(ContractModel):
    name: Identifier
    column: Identifier
    structure: IndexStructure
    supports_range: bool

    @model_validator(mode="after")
    def valid_range_support(self) -> Self:
        if self.supports_range != (self.structure == "bplus_unclustered"):
            raise ValueError("solo bplus_unclustered admite supports_range=true")
        return self


Columns = Annotated[tuple[Column, ...], BeforeValidator(_array)]
Indexes = Annotated[tuple[DeclaredIndex, ...], BeforeValidator(_array)]
Fragments = Annotated[tuple[Fragment, ...], BeforeValidator(_array)]
Nodes = Annotated[tuple[Node, ...], BeforeValidator(_array)]


def require_unique(values, label: str) -> None:
    seen = set()
    for value in values:
        if value in seen:
            raise ValueError(f"{label} duplicado: {value}")
        seen.add(value)


class DistributedTable(ContractModel):
    name: Identifier
    mode: Literal["range", "single"]
    storage: Storage
    primary_key: Identifier
    columns: Columns
    indexes: Indexes
    fragments: Fragments

    @property
    def bound_schema(self) -> BoundSchema:
        """Esquema reutilizable por el binder y el adaptador nativo existentes."""
        return BoundSchema(
            self.name,
            tuple(BoundColumn(c.name, SqlTypeName(c.type), c.size) for c in self.columns),
            next(i for i, c in enumerate(self.columns) if c.name == self.primary_key),
        )

    @property
    def ordered_fragments(self) -> tuple[Fragment, ...]:
        return tuple(
            sorted(
                self.fragments,
                key=lambda f: (f.lower is not None, f.lower if f.lower is not None else 0),
            )
        )

    @model_validator(mode="after")
    def consistent(self) -> Self:
        require_unique((c.name for c in self.columns), "columna")
        by_name = {c.name: c for c in self.columns}
        if self.primary_key not in by_name:
            raise ValueError("primary_key debe identificar una columna existente")
        if self.storage in {"heap", "sequential"}:
            # Reutiliza límites de registro/tipos del motor sin importar C++
            # ni reconstruir SQL (los nombres pueden coincidir con keywords).
            span = Span(0, 0, 1, 1, 1, 1)
            statement = CreateTableStatement(
                SqlIdentifier(self.name, span),
                tuple(
                    ColumnDefinition(
                        SqlIdentifier(c.name, span),
                        SqlType(SqlTypeName(c.type), c.size, span),
                        c.name == self.primary_key,
                        span,
                    )
                    for c in self.columns
                ),
                StorageKind(self.storage.upper()),
                span,
            )
            try:
                bind_create_table(statement)
            except SQLError as error:
                raise ValueError(error.message) from error
        require_unique((f.fragment_id for f in self.fragments), "fragmento")
        require_unique((f.node_id for f in self.fragments), "nodo de tabla")
        require_unique((i.name for i in self.indexes), "índice")
        if self.indexes and self.storage != "heap":
            raise ValueError("los índices secundarios requieren heap")
        for index in self.indexes:
            if index.column not in by_name:
                raise ValueError(f"columna inexistente para índice {index.name}")
            data_type = by_name[index.column].type
            required = {
                "rtree": "POINT",
                "inverted_index": "VARCHAR",
                "ivf": "VECTOR",
                "hnsw": "VECTOR",
            }.get(index.structure)
            if required is not None and data_type != required:
                raise ValueError(f"{index.structure} requiere {required}")
            if data_type == "POINT" and index.structure != "rtree":
                raise ValueError("POINT requiere rtree")
            if data_type == "VECTOR" and index.structure not in {"ivf", "hnsw"}:
                raise ValueError("VECTOR requiere ivf o hnsw")
            if self.mode == "range" and index.structure == "inverted_index":
                raise ValueError("FULLTEXT no se admite en tablas range v1")
        if self.mode == "single":
            if len(self.fragments) != 1:
                raise ValueError("single requiere exactamente un fragmento")
            if self.fragments[0].lower is not None or self.fragments[0].upper is not None:
                raise ValueError("single requiere ambos límites null")
        else:
            if self.storage != "heap" or by_name[self.primary_key].type != "INT":
                raise ValueError("range requiere heap y clave primaria INT")
            if len(self.fragments) < 2:
                raise ValueError("range requiere al menos dos fragmentos")
            ordered = self.ordered_fragments
            if ordered[0].lower is not None or ordered[-1].upper is not None:
                raise ValueError("los rangos deben cubrir todo el dominio INT32")
            for left, right in itertools.pairwise(ordered):
                if left.upper is None or right.lower is None or left.upper > right.lower:
                    raise ValueError("rangos superpuestos")
                if left.upper < right.lower:
                    raise ValueError("hueco entre rangos")
        return self


Tables = Annotated[tuple[DistributedTable, ...], BeforeValidator(_array)]
