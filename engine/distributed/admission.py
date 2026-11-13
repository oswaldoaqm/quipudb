"""Lista permitida de SQL v1 y pertenencia, utilizando el AST/binder del motor."""

from engine.distributed.node_errors import NodeError, sql_error
from engine.distributed.partitioning import owner_for_key
from engine.parser import parse_sql_script
from engine.parser.ast import (
    ComparisonCondition,
    DeleteStatement,
    ExplainStatement,
    InsertStatement,
    SelectStatement,
    TableRef,
    Wildcard,
)
from engine.parser.errors import SQLError
from engine.parser.semantic import bind_delete, bind_insert, bind_select


def _unsupported(message):
    raise NodeError("UNSUPPORTED_DISTRIBUTED", message)


def validate_sql(table, fragment, sql: str, *, scan: bool = False):
    """Valida antes de ejecutar. Devuelve AST, sin leer ni escribir archivos."""
    try:
        statements = parse_sql_script(sql)
        if len(statements) != 1:
            _unsupported("v1 admite exactamente una sentencia; no admite lotes")
        statement = statements[0]
        inner = statement.statement if isinstance(statement, ExplainStatement) else statement
        if isinstance(inner, SelectStatement):
            if not isinstance(inner.source, TableRef):
                _unsupported("JOIN no está admitido por el nodo v1")
            name = inner.source.table.name
        elif isinstance(inner, (InsertStatement, DeleteStatement)):
            name = inner.table.name
        else:
            _unsupported("DDL y transacciones no están admitidos por el nodo v1")
        if name != table.name:
            raise NodeError("WRONG_FRAGMENT", "la tabla SQL no coincide con el fragmento")
        if scan:
            if table.mode != "range" or not isinstance(statement, SelectStatement):
                _unsupported("scan requiere SELECT sobre una tabla range")
            if (
                len(statement.projections) != 1
                or not isinstance(statement.projections[0], Wildcard)
                or statement.group_by is not None
                or statement.order_by is not None
                or statement.limit is not None
            ):
                _unsupported("scan requiere SELECT * sin agrupación, orden ni límite")
            if statement.where is not None and not isinstance(statement.where, ComparisonCondition):
                _unsupported("scan solo admite una comparación escalar simple")
            bound = bind_select(statement, {table.name: table.bound_schema}, sql)
            if bound.where is not None and bound.where.column.column.data_type.value not in {
                "INT",
                "DOUBLE",
                "VARCHAR",
                "BOOL",
                "DATE",
            }:
                _unsupported("scan no admite predicados POINT/VECTOR")
        elif isinstance(statement, InsertStatement):
            bound = bind_insert(statement, table.bound_schema, sql)
            if table.mode == "range":
                _check_owner(table, fragment, bound.values[table.bound_schema.key_column])
        elif isinstance(statement, DeleteStatement):
            if table.mode == "range" and (
                not isinstance(statement.where, ComparisonCondition)
                or statement.where.operator != "="
                or statement.where.column.name.name != table.primary_key
            ):
                _unsupported("DELETE range requiere igualdad de clave primaria")
            bound = bind_delete(statement, table.bound_schema, sql)
            if table.mode == "range":
                _check_owner(table, fragment, bound.where.value)
        elif table.mode == "single" and isinstance(inner, SelectStatement):
            bind_select(inner, {table.name: table.bound_schema}, sql)
        else:
            _unsupported("execute range solo admite INSERT y DELETE por PK; usa scan para leer")
        return statement
    except SQLError as error:
        raise sql_error(error) from error


def _check_owner(table, fragment, key):
    if owner_for_key(table, key) != fragment:
        raise NodeError("WRONG_FRAGMENT", "la clave primaria pertenece a otro fragmento")
