"""Errores del nodo con el sobre v1, sin dependencias HTTP."""

from engine.api.service import to_error
from engine.distributed.protocol import ErrorResponse, ExecutionError
from engine.parser.errors import SQLError

STATUS = {
    "INVALID_REQUEST": 422,
    "PROTOCOL_VERSION_MISMATCH": 409,
    "CATALOG_VERSION_MISMATCH": 409,
    "SCHEMA_MISMATCH": 409,
    "FRAGMENT_NOT_FOUND": 404,
    "WRONG_FRAGMENT": 409,
    "UNSUPPORTED_DISTRIBUTED": 400,
    "SQL_ERROR": 400,
    "DUPLICATE_KEY": 409,
    "NODE_NOT_READY": 503,
    "EXECUTION_ERROR": 500,
}


class NodeError(Exception):
    def __init__(self, code: str, message: str, *, state: str = "not_started", sql=None):
        super().__init__(message)
        self.code = code
        self.status = STATUS[code]
        self.detail = ExecutionError(
            code=code,
            message=message,
            execution_state=state,
            kind=sql.kind if sql else None,
            line=sql.line if sql else None,
            column=sql.column if sql else None,
            end_line=sql.end_line if sql else None,
            end_column=sql.end_column if sql else None,
        )

    def response(self, catalog, node_id, request_id=None, fragment_id=None) -> ErrorResponse:
        return ErrorResponse(
            protocol_version=catalog.protocol_version,
            catalog_version=catalog.catalog_version,
            node_id=node_id,
            request_id=request_id,
            fragment_id=fragment_id,
            error=self.detail,
        )


def sql_error(error: SQLError, *, state="not_started", native=None) -> NodeError:
    # QueryProcessor preserva la excepción C++ como __cause__; nunca se clasifica
    # DuplicateKey por coincidencias de texto en el mensaje.
    duplicate = native is not None and isinstance(error.__cause__, native.DuplicateKey)
    return NodeError(
        "DUPLICATE_KEY" if duplicate else "SQL_ERROR",
        error.message,
        state=state,
        sql=to_error(error),
    )
