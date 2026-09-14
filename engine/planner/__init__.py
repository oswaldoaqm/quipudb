"""Planner: decide como ejecutar una consulta y describe el plan resultante.

El plan que se produce aqui es lo que consume el Panel de Plan de Ejecucion del
frontend (seccion 2.1.5), por eso la estructura del plan es un contrato entre
el core, el parser y el frontend, y no un detalle interno.
"""

from engine.planner.native_catalog import from_native_table_info, load_table_metadata
from engine.planner.optimizer import (
    AccessRoute,
    IndexMetadata,
    PhysicalSelectPlan,
    TableMetadata,
    optimize_select,
)

__all__ = [
    "AccessRoute",
    "IndexMetadata",
    "PhysicalSelectPlan",
    "TableMetadata",
    "from_native_table_info",
    "load_table_metadata",
    "optimize_select",
]
