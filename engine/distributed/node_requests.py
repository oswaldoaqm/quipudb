"""Validación del sobre antes de tocar el motor o aceptar SQL."""

import json

from pydantic import TypeAdapter, ValidationError

from engine.distributed.catalog import _invalid_constant, _unique_object
from engine.distributed.contracts import EntityId
from engine.distributed.node_errors import NodeError
from engine.distributed.protocol import BaseRequest, FragmentRequest, RequestId

_REQUEST_ID = TypeAdapter(RequestId)
_ENTITY_ID = TypeAdapter(EntityId)


def request_id_of(data) -> str | None:
    try:
        return _REQUEST_ID.validate_python(data.get("request_id"), strict=True)
    except (AttributeError, ValidationError):
        return None


def fragment_id_of(value: str) -> str:
    try:
        return _ENTITY_ID.validate_python(value, strict=True)
    except ValidationError as error:
        raise NodeError("INVALID_REQUEST", "fragment_id inválido") from error


def decode_body(body: bytes) -> dict:
    try:
        data = json.loads(
            body.decode("utf-8"), object_pairs_hook=_unique_object, parse_constant=_invalid_constant
        )
        if not isinstance(data, dict):
            raise NodeError("INVALID_REQUEST", "el cuerpo debe ser un objeto JSON")
        return data
    except (UnicodeError, ValueError) as error:
        raise NodeError("INVALID_REQUEST", str(error)) from error


def validate_request(data: dict, catalog, *, fragment: bool = False):
    # El modelo v1 rechaza versiones distintas; discriminarlas antes para
    # devolver el 409 contractual en vez de un 422 genérico de Pydantic.
    for field, code, expected in (
        ("protocol_version", "PROTOCOL_VERSION_MISMATCH", catalog.protocol_version),
        ("catalog_version", "CATALOG_VERSION_MISMATCH", catalog.catalog_version),
    ):
        value = data.get(field)
        if type(value) is not int or value < 1:
            raise NodeError("INVALID_REQUEST", f"{field} requiere un entero positivo")
        if value != expected:
            raise NodeError(code, f"se recibió {field}={value}; el nodo requiere {expected}")
    model = FragmentRequest if fragment else BaseRequest
    try:
        return model.model_validate(data)
    except ValidationError as error:
        raise NodeError("INVALID_REQUEST", str(error)) from error


def resolve_fragment(catalog, node_id: str, fragment_id: str, table_name: str):
    fragment_id_of(fragment_id)
    for table in catalog.tables:
        for fragment in table.fragments:
            if fragment.fragment_id == fragment_id:
                if fragment.node_id != node_id or table.name != table_name:
                    raise NodeError("WRONG_FRAGMENT", "el fragmento no corresponde al nodo o tabla")
                return table, fragment
    raise NodeError("FRAGMENT_NOT_FOUND", f"fragmento inexistente: {fragment_id}")
