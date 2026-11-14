"""Carga estricta del catálogo persistente; no abre archivos del motor."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Self

from pydantic import ValidationError, field_validator, model_validator

from engine.distributed.contracts import (
    PROTOCOL_VERSION,
    ContractModel,
    DistributedTable,
    Fragment,
    Node,
    Nodes,
    PositiveInt,
    Tables,
    require_unique,
)


class CatalogError(ValueError):
    """Archivo ilegible, JSON inválido o metadatos incoherentes."""


class DistributedCatalog(ContractModel):
    protocol_version: PositiveInt
    catalog_version: PositiveInt
    nodes: Nodes
    tables: Tables

    @field_validator("protocol_version")
    @classmethod
    def supported_protocol(cls, value: int) -> int:
        if value != PROTOCOL_VERSION:
            raise ValueError(f"protocol_version incompatible: se requiere {PROTOCOL_VERSION}")
        return value

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if len(self.nodes) < 2:
            raise ValueError("v1 requiere al menos dos nodos independientes")
        require_unique((n.node_id for n in self.nodes), "nodo")
        require_unique((n.base_url for n in self.nodes), "base_url")
        require_unique((t.name for t in self.tables), "tabla")
        require_unique(
            (f.fragment_id for t in self.tables for f in t.fragments),
            "fragmento global",
        )
        nodes = {n.node_id for n in self.nodes}
        for table in self.tables:
            for fragment in table.fragments:
                if fragment.node_id not in nodes:
                    raise ValueError(f"nodo inexistente: {fragment.node_id}")
        return self

    def table(self, name: str) -> DistributedTable:
        for table in self.tables:
            if table.name == name:
                return table
        raise KeyError(f"tabla inexistente: {name}")

    def node(self, node_id: str) -> Node:
        for node in self.nodes:
            if node.node_id == node_id:
                return node
        raise KeyError(f"nodo inexistente: {node_id}")

    def fragments_for_node(self, node_id: str) -> tuple[Fragment, ...]:
        self.node(node_id)
        return tuple(f for t in self.tables for f in t.ordered_fragments if f.node_id == node_id)


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"campo JSON duplicado: {key}")
        result[key] = value
    return result


def _invalid_constant(value: str):
    raise ValueError(f"constante no JSON: {value}")


def load_catalog(
    path: str | Path,
    *,
    expected_catalog_version: int | None = None,
) -> DistributedCatalog:
    """Lee sin modificar el archivo; opcionalmente exige una versión de despliegue.

    catalog_version no está fijado en 1: toda versión positiva es válida, pero
    dos participantes de un despliegue deben exigir el mismo valor.
    """
    if expected_catalog_version is not None and (
        type(expected_catalog_version) is not int or expected_catalog_version < 1
    ):
        raise CatalogError("expected_catalog_version debe ser entero positivo")
    try:
        data = json.loads(
            Path(path).read_text(encoding="utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_invalid_constant,
        )
        catalog = DistributedCatalog.model_validate(data)
        if (
            expected_catalog_version is not None
            and catalog.catalog_version != expected_catalog_version
        ):
            raise ValueError(
                f"catalog_version incompatible: {catalog.catalog_version}; "
                f"se requiere {expected_catalog_version}"
            )
        return catalog
    except (OSError, UnicodeError, ValueError, ValidationError) as error:
        raise CatalogError(f"catálogo {path}: {error}") from error
