"""Sobres HTTP/JSON v1 compartidos; solo modelos, sin endpoints ni transporte."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import AfterValidator, ConfigDict, field_validator

from engine.api.schemas import ErrorKind, QueryResponse, TableInfo
from engine.distributed.contracts import (
    PROTOCOL_VERSION,
    ContractModel,
    EntityId,
    Identifier,
    PositiveInt,
)


def _uuid(value: str) -> str:
    if str(UUID(value)) != value:
        raise ValueError("request_id requiere UUID canónico en minúsculas")
    return value


RequestId = Annotated[str, AfterValidator(_uuid)]


class Versioned(ContractModel):
    protocol_version: PositiveInt
    catalog_version: PositiveInt

    @field_validator("protocol_version")
    @classmethod
    def supported(cls, value: int) -> int:
        if value != PROTOCOL_VERSION:
            raise ValueError("protocol_version incompatible")
        return value


class BaseRequest(Versioned):
    request_id: RequestId


class FragmentRequest(BaseRequest):
    table: Identifier
    sql: str


class Response(Versioned):
    model_config = ConfigDict(extra="ignore")
    node_id: EntityId


class HealthResponse(Response):
    status: Literal["ready", "not_ready"]
    fragment_ids: list[EntityId]
    reason: str | None


class FragmentInfo(ContractModel):
    fragment_id: EntityId
    table_info: TableInfo


class CatalogResponse(Response):
    request_id: RequestId
    fragments: list[FragmentInfo]


class ExecutionResponse(Response):
    request_id: RequestId
    fragment_id: EntityId
    result: QueryResponse


class ExecutionError(ContractModel):
    code: Literal[
        "INVALID_REQUEST",
        "PROTOCOL_VERSION_MISMATCH",
        "CATALOG_VERSION_MISMATCH",
        "SCHEMA_MISMATCH",
        "FRAGMENT_NOT_FOUND",
        "WRONG_FRAGMENT",
        "UNSUPPORTED_DISTRIBUTED",
        "SQL_ERROR",
        "DUPLICATE_KEY",
        "NODE_NOT_READY",
        "EXECUTION_ERROR",
    ]
    message: str
    execution_state: Literal["not_started", "unknown"]
    kind: ErrorKind | None
    line: PositiveInt | None
    column: PositiveInt | None
    end_line: PositiveInt | None
    end_column: PositiveInt | None


class ErrorResponse(Response):
    request_id: RequestId | None
    fragment_id: EntityId | None
    error: ExecutionError
