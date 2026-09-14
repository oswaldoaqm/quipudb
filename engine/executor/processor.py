"""Fachada del procesamiento y la ejecucion de consultas SQL."""

from __future__ import annotations

from pathlib import Path
from time import perf_counter_ns
from typing import Any

from engine.executor.dml import execute_delete, insert_with_indexes
from engine.executor.external import ExternalExecutionOptions, execute_external_select
from engine.executor.native import (
    from_native_schema,
    load_native,
    to_native_schema,
    to_native_values,
)
from engine.executor.operators import execute_select
from engine.executor.result import QueryResult
from engine.parser import (
    CreateTableStatement,
    DeleteStatement,
    InsertStatement,
    SelectStatement,
    SQLSemanticError,
    parse_sql,
)
from engine.parser.semantic import bind_create_table, bind_delete, bind_insert, bind_select
from engine.planner.native_catalog import from_native_table_info
from engine.planner.optimizer import optimize_delete, optimize_select
from engine.planner.plan import Plan


class QueryProcessor:
    """Parsea, valida y ejecuta el subconjunto SQL disponible."""

    def __init__(
        self,
        database: Any,
        native_module: Any | None = None,
        *,
        external_buffers: int = 64,
        external_page_size: int = 4096,
        temp_dir: str | Path | None = None,
    ) -> None:
        self._database = database
        self._native = native_module if native_module is not None else load_native()
        external_temp_dir = None if temp_dir is None else Path(temp_dir)
        if external_temp_dir is not None:
            external_temp_dir.mkdir(parents=True, exist_ok=True)
        self._external_options = ExternalExecutionOptions(
            buffers=external_buffers,
            page_size=external_page_size,
            temp_dir=external_temp_dir,
        )
        self._domain_errors = tuple(
            error_type
            for name in ("SchemaError", "InvalidRecord", "DuplicateKey")
            if isinstance((error_type := getattr(self._native, name, None)), type)
            and issubclass(error_type, BaseException)
        )

    def execute(self, source: str) -> QueryResult:
        """Ejecuta una sentencia del subconjunto disponible."""

        started_ns = perf_counter_ns()
        statement = parse_sql(source)
        if isinstance(statement, CreateTableStatement):
            return self._create_table(statement, source)
        if isinstance(statement, InsertStatement):
            return self._insert(statement, source)
        if isinstance(statement, SelectStatement):
            return self._select(statement, source, started_ns)
        if isinstance(statement, DeleteStatement):
            return self._delete(statement, source)
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

    def _select(
        self,
        statement: SelectStatement,
        source: str,
        started_ns: int,
    ) -> QueryResult:
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
        bound = bind_select(statement, schema, source)
        physical_plan = optimize_select(bound, from_native_table_info(table_info))
        try:
            if bound.group_by is not None or bound.order_by is not None:
                execution = execute_external_select(
                    self._database,
                    self._native,
                    physical_plan,
                    source,
                    self._external_options,
                )
            else:
                execution = execute_select(self._database, self._native, physical_plan, source)
        except self._domain_errors as error:
            self._raise_semantic(
                error,
                f"no se pudo consultar la tabla {statement.table.name!r}",
                statement.table.span,
                source,
            )

        elapsed_ms = (perf_counter_ns() - started_ns) / 1_000_000
        plan = Plan(query=source, root=execution.root, time_ms=elapsed_ms)
        return QueryResult(
            columns=execution.columns,
            rows=execution.rows,
            plan=plan,
        )

    def _delete(self, statement: DeleteStatement, source: str) -> QueryResult:
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
        bound = bind_delete(statement, schema, source)
        physical_plan = optimize_delete(bound, from_native_table_info(table_info))
        try:
            affected_rows = execute_delete(self._database, self._native, physical_plan)
        except self._domain_errors as error:
            self._raise_semantic(
                error,
                f"no se pudo borrar de la tabla {statement.table.name!r}",
                statement.where.span,
                source,
            )
        return QueryResult(affected_rows=affected_rows)

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
