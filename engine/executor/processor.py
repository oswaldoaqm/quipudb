"""Fachada del procesamiento y la ejecucion de consultas SQL."""

from __future__ import annotations

from typing import Any

from engine.executor.dml import insert_with_indexes
from engine.executor.native import (
    from_native_schema,
    load_native,
    to_native_schema,
    to_native_values,
)
from engine.executor.result import QueryResult
from engine.parser import (
    CreateTableStatement,
    DeleteStatement,
    InsertStatement,
    SelectStatement,
    SQLSemanticError,
    SQLUnsupportedError,
    parse_sql,
)
from engine.parser.semantic import bind_create_table, bind_insert


class QueryProcessor:
    """Parsea, valida y ejecuta el subconjunto SQL disponible."""

    def __init__(self, database: Any, native_module: Any | None = None) -> None:
        self._database = database
        self._native = native_module if native_module is not None else load_native()
        self._domain_errors = tuple(
            error_type
            for name in ("SchemaError", "InvalidRecord", "DuplicateKey")
            if isinstance((error_type := getattr(self._native, name, None)), type)
            and issubclass(error_type, BaseException)
        )

    def execute(self, source: str) -> QueryResult:
        """Ejecuta una sentencia; SELECT y DELETE llegan en sus propios issues."""

        statement = parse_sql(source)
        if isinstance(statement, CreateTableStatement):
            return self._create_table(statement, source)
        if isinstance(statement, InsertStatement):
            return self._insert(statement, source)
        if isinstance(statement, SelectStatement):
            raise SQLUnsupportedError(
                "la ejecucion de SELECT se implementa en el issue #26",
                statement.span,
                source,
            )
        if isinstance(statement, DeleteStatement):
            raise SQLUnsupportedError(
                "la ejecucion de DELETE se implementa en el issue #27",
                statement.span,
                source,
            )
        raise AssertionError(f"sentencia sin ejecutor: {type(statement).__name__}")

    def _create_table(
        self,
        statement: CreateTableStatement,
        source: str,
    ) -> QueryResult:
        bound = bind_create_table(statement, source)
        schema = to_native_schema(bound.schema, self._native)
        storage = {
            "HEAP": self._native.kind.HEAP,
            "SEQUENTIAL": self._native.kind.SEQUENTIAL,
        }[bound.storage.value]

        try:
            self._database.create_table(schema, storage)
        except self._domain_errors as error:
            self._raise_semantic(
                error,
                f"no se pudo crear la tabla {statement.table.name!r}",
                statement.table.span,
                source,
            )
        return QueryResult()

    def _insert(self, statement: InsertStatement, source: str) -> QueryResult:
        try:
            table_info = self._database.table_info(statement.table.name)
        except self._domain_errors as error:
            self._raise_semantic(
                error,
                f"la tabla {statement.table.name!r} no existe",
                statement.table.span,
                source,
            )

        schema = from_native_schema(table_info.schema, self._native)
        bound = bind_insert(statement, schema, source)
        values = to_native_values(bound.values, self._native)

        try:
            insert_with_indexes(
                self._database,
                bound.table_name,
                values,
                schema.key_column,
                list(table_info.indexes),
            )
        except self._domain_errors as error:
            key_span = statement.values[schema.key_column].span
            self._raise_semantic(
                error,
                f"no se pudo insertar en la tabla {statement.table.name!r}",
                key_span,
                source,
            )
        return QueryResult(affected_rows=1)

    @staticmethod
    def _raise_semantic(
        error: BaseException,
        message: str,
        span: Any,
        source: str,
    ) -> None:
        detail = str(error)
        if detail:
            message = f"{message}: {detail}"
        raise SQLSemanticError(message, span, source) from error
