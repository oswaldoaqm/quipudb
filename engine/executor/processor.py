"""Fachada del procesamiento y la ejecucion de consultas SQL."""

from __future__ import annotations

import csv
from pathlib import Path
from time import perf_counter_ns
from typing import Any, TextIO

from engine.executor.bulk_load import (
    CsvHeader,
    CsvLoadError,
    LoadReport,
    convert_row,
    match_header,
    read_header,
)
from engine.executor.dml import collect_delete_candidates, delete_with_indexes, insert_with_indexes
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
    BeginTransactionStatement,
    CreateIndexStatement,
    CreateTableStatement,
    DeleteStatement,
    DropTableStatement,
    EndTransactionStatement,
    ExplainStatement,
    FromSource,
    InsertStatement,
    JoinRef,
    SelectStatement,
    SQLSemanticError,
    Statement,
    TableRef,
    parse_sql_script,
)
from engine.parser.bound_ast import BoundSchema, BoundSelectStatement
from engine.parser.semantic import (
    bind_create_index,
    bind_create_table,
    bind_delete,
    bind_drop_table,
    bind_insert,
    bind_select,
)
from engine.planner.explain import explain_select
from engine.planner.native_catalog import from_native_table_info
from engine.planner.optimizer import (
    PhysicalSelectPlan,
    TableMetadata,
    optimize_delete,
    optimize_select,
)
from engine.planner.plan import Plan
from engine.transactions import (
    DEFAULT_LOCK_TIMEOUT,
    LockManager,
    LockMode,
    Transaction,
    TransactionError,
)


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
        lock_manager: LockManager | None = None,
        lock_timeout: float = DEFAULT_LOCK_TIMEOUT,
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
        self._transaction: Transaction | None = None
        # Un `LockManager` propio por defecto deja el locking en no-op: nadie
        # mas lo comparte. Para concurrencia real (ADR 0005), varias
        # instancias de QueryProcessor (una por hilo/conexion) reciben el
        # MISMO LockManager.
        self._lock_manager = lock_manager if lock_manager is not None else LockManager()
        self._lock_timeout = lock_timeout
        self._autocommit_owner = object()

    def execute(self, source: str) -> QueryResult:
        """Ejecuta una o varias sentencias separadas por punto y coma.

        Todo el script se analiza antes de ejecutar. En un lote, las sentencias
        se aplican en orden, ``affected_rows`` se suma y las filas y el plan
        corresponden a la ultima sentencia. Para atomicidad ante errores de
        ejecucion, el llamador debe usar una transaccion explicita.
        """

        started_ns = perf_counter_ns()
        statements = parse_sql_script(source)
        results = [
            self._execute_statement(
                statement,
                source,
                started_ns if index == 0 else perf_counter_ns(),
                source
                if len(statements) == 1
                else source[statement.span.start : statement.span.end],
            )
            for index, statement in enumerate(statements)
        ]
        if len(results) == 1:
            return results[0]

        last = results[-1]
        return QueryResult(
            columns=last.columns,
            column_types=last.column_types,
            rows=last.rows,
            affected_rows=sum(result.affected_rows for result in results),
            plan=last.plan,
        )

    def _execute_statement(
        self,
        statement: Statement,
        source: str,
        started_ns: int,
        query_text: str,
    ) -> QueryResult:
        if isinstance(statement, CreateTableStatement):
            return self._create_table(statement, source)
        if isinstance(statement, CreateIndexStatement):
            return self._create_index(statement, source)
        if isinstance(statement, InsertStatement):
            return self._insert(statement, source)
        if isinstance(statement, SelectStatement):
            return self._select(statement, source, started_ns, query_text)
        if isinstance(statement, ExplainStatement):
            return self._explain(statement, source, started_ns)
        if isinstance(statement, DeleteStatement):
            return self._delete(statement, source)
        if isinstance(statement, DropTableStatement):
            return self._drop_table(statement, source)
        if isinstance(statement, BeginTransactionStatement):
            return self._begin_transaction()
        if isinstance(statement, EndTransactionStatement):
            return self._end_transaction()
        raise AssertionError(f"sentencia sin ejecutor: {type(statement).__name__}")

    def rollback(self) -> None:
        """Deshace la transaccion activa y la abandona.

        Simula un cliente que abandona una transaccion sin mandar
        `END TRANSACTION` (no hay `ROLLBACK` en el subconjunto SQL). Lanza
        `TransactionError` si no hay ninguna transaccion activa.
        """

        if self._transaction is None:
            raise TransactionError("no hay una transaccion activa para deshacer")
        self._transaction.rollback()
        self._transaction = None

    def load_csv(self, table_name: str, stream: TextIO, *, atomic: bool = False) -> LoadReport:
        """Carga en ``table_name`` las filas de un CSV con cabecera (#114).

        El archivo se lee fila por fila: lo unico que crece con su tamano es
        la bitacora de deshacer cuando ``atomic`` es verdadero. La cabecera se
        empareja con el esquema antes de insertar nada, asi que un CSV de otra
        tabla se rechaza entero con ``CsvLoadError``.

        Sin ``atomic``, cada fila es su propia unidad: las que no se pueden
        convertir o insertar se cuentan y se reportan con su linea, y las demas
        quedan. Con ``atomic``, la primera fila que falla deshace todas las
        anteriores y se lanza ``CsvLoadError`` con esa linea.

        No se puede cargar dentro de un ``BEGIN TRANSACTION``: la carga toma su
        propio lock exclusivo de la tabla durante todo el archivo.
        """

        if self._transaction is not None:
            raise TransactionError(
                "la carga masiva no se puede ejecutar dentro de una transaccion: "
                "falta un END TRANSACTION"
            )
        try:
            table_info = self._database.table_info(table_name)
        except self._domain_errors:
            raise CsvLoadError(f"la tabla {table_name!r} no existe") from None

        schema = from_native_schema(table_info.schema, self._native)
        index_metadata = list(table_info.indexes)
        try:
            header = read_header(stream)
        except UnicodeDecodeError as error:
            raise CsvLoadError(f"no se pudo leer la cabecera: {error}", line=1) from None
        mapping = match_header(header.fields, schema, line=header.line)
        reader = csv.reader(stream, delimiter=header.delimiter, strict=True)

        report = LoadReport(table_name)
        transaction = Transaction(self._database, self._lock_manager) if atomic else None
        if transaction is not None:
            transaction.ensure_lock(table_name, LockMode.EXCLUSIVE, self._lock_timeout)
        else:
            self._lock_manager.acquire(
                table_name, self._autocommit_owner, LockMode.EXCLUSIVE, self._lock_timeout
            )

        try:
            while True:
                # La linea donde EMPIEZA la fila: un texto entre comillas puede
                # ocupar varias, y es la primera la que el usuario busca.
                line = header.line + reader.line_num + 1
                try:
                    fields = next(reader)
                except StopIteration:
                    break
                except csv.Error as error:
                    error_message = f"CSV mal formado: {error}"
                except UnicodeDecodeError:
                    # No se puede saber donde vuelve a ser texto valido, asi
                    # que el resto del archivo no se lee.
                    error_message = "el archivo deja de ser UTF-8 valido; el resto no se leyo"
                    if transaction is None:
                        report.fail(line, error_message)
                        break
                else:
                    if not fields:
                        continue  # linea en blanco
                    error_message = self._load_row(
                        table_name, fields, mapping, schema, header, index_metadata, transaction
                    )
                    if error_message is None:
                        report.inserted += 1
                        continue

                if transaction is not None:
                    raise CsvLoadError(
                        f"linea {line}: {error_message}. La carga es atomica y no se "
                        "inserto ninguna fila",
                        line=line,
                    )
                report.fail(line, error_message)
        except BaseException:
            if transaction is not None:
                transaction.rollback()
            raise
        else:
            if transaction is not None:
                transaction.commit()
        finally:
            if transaction is None:
                self._lock_manager.release(table_name, self._autocommit_owner)
        return report

    def _load_row(
        self,
        table_name: str,
        fields: list[str],
        mapping: tuple[int, ...],
        schema: BoundSchema,
        header: CsvHeader,
        index_metadata: list[Any],
        transaction: Transaction | None,
    ) -> str | None:
        """Inserta una fila del CSV; devuelve por que no entro, o ``None``."""

        try:
            row = convert_row(
                fields,
                mapping,
                schema,
                len(header.fields),
                decimal_comma=header.delimiter == ";",
            )
            values = to_native_values(tuple(row), self._native)
        except ValueError as error:
            return str(error)
        try:
            rid = insert_with_indexes(
                self._database, table_name, values, schema.key_column, index_metadata
            )
        except self._domain_errors as error:
            return f"no se pudo insertar: {error}"
        if transaction is not None:
            transaction.record_insert(table_name, values, rid, schema.key_column, index_metadata)
        return None

    def _begin_transaction(self) -> QueryResult:
        if self._transaction is not None:
            raise TransactionError("ya hay una transaccion activa: falta un END TRANSACTION")
        self._transaction = Transaction(self._database, self._lock_manager)
        return QueryResult()

    def _end_transaction(self) -> QueryResult:
        if self._transaction is None:
            raise TransactionError("no hay una transaccion activa: falta un BEGIN TRANSACTION")
        self._transaction.commit()
        self._transaction = None
        return QueryResult()

    def _fail_in_transaction(self) -> None:
        """Si hay una transaccion activa, la deshace y la abandona.

        Se llama antes de relanzar cualquier error de un INSERT o DELETE (o de
        no poder adquirir un lock) para que una transaccion nunca quede a
        medio aplicar ni reteniendo locks que ya no va a usar (ADR 0004,
        ADR 0005).
        """

        if self._transaction is not None:
            self._transaction.rollback()
            self._transaction = None

    def _lock_or_abort(self, table_name: str, mode: LockMode) -> None:
        """Adquiere el lock de ``table_name`` antes de tocar sus datos.

        Con una transaccion activa, el lock se retiene hasta que confirme o
        se deshaga (2PL estricto, ADR 0005). Sin transaccion, es responsabi-
        lidad de quien llama liberarlo con `_release_autocommit` al terminar
        la sentencia. Un timeout de lock aborta la transaccion activa, igual
        que cualquier otro error de una mutacion.
        """

        try:
            if self._transaction is not None:
                self._transaction.ensure_lock(table_name, mode, self._lock_timeout)
            else:
                self._lock_manager.acquire(
                    table_name, self._autocommit_owner, mode, self._lock_timeout
                )
        except Exception:
            self._fail_in_transaction()
            raise

    def _release_autocommit(self, table_name: str) -> None:
        """Libera el lock tomado por `_lock_or_abort` fuera de una transaccion."""

        if self._transaction is None:
            self._lock_manager.release(table_name, self._autocommit_owner)

    def _create_table(
        self,
        statement: CreateTableStatement,
        source: str,
    ) -> QueryResult:
        if self._transaction is not None:
            raise TransactionError("CREATE TABLE no se puede ejecutar dentro de una transaccion")
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
        index_metadata = list(table_info.indexes)

        self._lock_or_abort(bound.table_name, LockMode.EXCLUSIVE)
        try:
            try:
                rid = insert_with_indexes(
                    self._database,
                    bound.table_name,
                    values,
                    schema.key_column,
                    index_metadata,
                )
            except self._domain_errors as error:
                self._fail_in_transaction()
                key_span = statement.values[schema.key_column].span
                self._raise_semantic(
                    error,
                    f"no se pudo insertar en la tabla {statement.table.name!r}",
                    key_span,
                    source,
                )
            except Exception:
                self._fail_in_transaction()
                raise

            if self._transaction is not None:
                self._transaction.record_insert(
                    bound.table_name, values, rid, schema.key_column, index_metadata
                )
            return QueryResult(affected_rows=1)
        finally:
            self._release_autocommit(bound.table_name)

    def _create_index(
        self,
        statement: CreateIndexStatement,
        source: str,
    ) -> QueryResult:
        if self._transaction is not None:
            raise TransactionError("CREATE INDEX no se puede ejecutar dentro de una transaccion")
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
        bound = bind_create_index(statement, schema, source)
        kind = {
            "BPLUS_UNCLUSTERED": self._native.kind.BPLUS_UNCLUSTERED,
            "EXTENDIBLE_HASH": self._native.kind.EXTENDIBLE_HASH,
        }[bound.kind.value]

        self._lock_or_abort(bound.table_name, LockMode.EXCLUSIVE)
        try:
            try:
                self._database.create_index(
                    bound.table_name,
                    bound.index_name,
                    bound.column_name,
                    kind,
                )
            except self._domain_errors as error:
                self._raise_semantic(
                    error,
                    f"no se pudo crear el indice {bound.index_name!r}",
                    statement.index.span,
                    source,
                )
            return QueryResult()
        finally:
            self._release_autocommit(bound.table_name)

    def _drop_table(self, statement: DropTableStatement, source: str) -> QueryResult:
        if self._transaction is not None:
            raise TransactionError("DROP TABLE no se puede ejecutar dentro de una transaccion")
        bound = bind_drop_table(statement, source)

        self._lock_or_abort(bound.table_name, LockMode.EXCLUSIVE)
        try:
            try:
                self._database.drop_table(bound.table_name)
            except self._domain_errors as error:
                self._raise_semantic(
                    error,
                    f"no se pudo eliminar la tabla {bound.table_name!r}",
                    statement.table.span,
                    source,
                )
            return QueryResult()
        finally:
            self._release_autocommit(bound.table_name)

    def _select(
        self,
        statement: SelectStatement,
        source: str,
        started_ns: int,
        query_text: str,
    ) -> QueryResult:
        bound, physical_plan, referencias, esquemas = self._prepare_select(statement, source)

        primera = referencias[0].table
        # Orden alfabetico, no el del FROM: dos consultas concurrentes sobre el
        # mismo par de tablas se interbloquearian si cada una los tomara en su
        # propio orden. Ver el contrato acordado en el issue #96.
        bloqueadas = sorted(esquemas)
        tomadas: list[str] = []
        try:
            for nombre in bloqueadas:
                self._lock_or_abort(nombre, LockMode.SHARED)
                tomadas.append(nombre)

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
                    execution = execute_select(
                        self._database,
                        self._native,
                        physical_plan,
                        source,
                        self._external_options.temp_dir,
                    )
            except self._domain_errors as error:
                self._raise_semantic(
                    error,
                    f"no se pudo consultar la tabla {primera.name!r}",
                    primera.span,
                    source,
                )

            elapsed_ms = (perf_counter_ns() - started_ns) / 1_000_000
            plan = Plan(query=query_text, root=execution.root, time_ms=elapsed_ms)
            return QueryResult(
                columns=execution.columns,
                column_types=execution.column_types,
                rows=execution.rows,
                plan=plan,
            )
        finally:
            for nombre in reversed(tomadas):
                self._release_autocommit(nombre)

    def _prepare_select(
        self,
        statement: SelectStatement,
        source: str,
    ) -> tuple[BoundSelectStatement, PhysicalSelectPlan, list[TableRef], dict[str, BoundSchema]]:
        referencias = _table_refs(statement.source)
        esquemas: dict[str, BoundSchema] = {}
        metadata: dict[str, TableMetadata] = {}
        for referencia in referencias:
            try:
                table_info = self._database.table_info(referencia.table.name)
                filas = int(self._database.table(referencia.table.name).size())
            except self._domain_errors as error:
                self._raise_semantic(
                    error,
                    f"la tabla {referencia.table.name!r} no existe",
                    referencia.table.span,
                    source,
                )
            esquemas[referencia.table.name] = from_native_schema(table_info.schema, self._native)
            metadata[referencia.table.name] = from_native_table_info(table_info, rows=filas)

        bound = bind_select(statement, esquemas, source)
        physical_plan = optimize_select(bound, metadata)
        return bound, physical_plan, referencias, esquemas

    def _explain(
        self,
        statement: ExplainStatement,
        source: str,
        started_ns: int,
    ) -> QueryResult:
        query = source[statement.statement.span.start : statement.statement.span.end]
        if statement.analyze:
            analyzed = self._select(statement.statement, source, started_ns, query)
            return QueryResult(plan=analyzed.plan)

        _, physical_plan, _, _ = self._prepare_select(statement.statement, source)
        elapsed_ms = (perf_counter_ns() - started_ns) / 1_000_000
        return QueryResult(
            plan=explain_select(
                physical_plan,
                query,
                source,
                planning_ms=elapsed_ms,
            )
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

        self._lock_or_abort(statement.table.name, LockMode.EXCLUSIVE)
        try:
            try:
                candidates = collect_delete_candidates(
                    self._database, self._native, physical_plan
                )
                affected_rows = delete_with_indexes(
                    self._database,
                    physical_plan.statement.schema.table_name,
                    candidates,
                    physical_plan.statement.schema.key_column,
                    list(physical_plan.table.indexes),
                )
            except self._domain_errors as error:
                self._fail_in_transaction()
                self._raise_semantic(
                    error,
                    f"no se pudo borrar de la tabla {statement.table.name!r}",
                    statement.where.span,
                    source,
                )
            except Exception:
                self._fail_in_transaction()
                raise

            if self._transaction is not None and candidates:
                self._transaction.record_delete(
                    physical_plan.statement.schema.table_name,
                    candidates,
                    physical_plan.statement.schema.key_column,
                    list(physical_plan.table.indexes),
                )
            return QueryResult(affected_rows=affected_rows)
        finally:
            self._release_autocommit(statement.table.name)

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


def _table_refs(source: FromSource) -> list[TableRef]:
    """Las hojas del ``FROM``, de izquierda a derecha."""

    if isinstance(source, TableRef):
        return [source]
    if not isinstance(source, JoinRef):
        raise TypeError(f"fuente desconocida en el FROM: {type(source).__name__}")
    return _table_refs(source.left) + _table_refs(source.right)
