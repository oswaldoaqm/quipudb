"""DDL distribuido (#296): de una sentencia SQL a un catálogo nuevo, y de vuelta.

El coordinador recibe ``CREATE TABLE``, ``CREATE INDEX`` o ``DROP TABLE`` y
calcula aquí, sin red ni disco, el catálogo que resultaría. Cada error sale con
la posición de la parte de la sentencia que lo causa, igual que un error del
motor local. Después los nodos reciben el catálogo entero y cada uno aplica en
su motor solo lo que le toca (``changes_for_node``), con el SQL que se genera
aquí desde el catálogo.

Sintaxis, en una sola sentencia::

    CREATE TABLE ventas (id INT PRIMARY KEY, monto DOUBLE) USING HEAP
    PARTITION BY RANGE (id) (
      PARTITION ventas_f0 VALUES LESS THAN (5000) ON n1,
      PARTITION ventas_f1 VALUES LESS THAN (MAXVALUE) ON n2
    );
    CREATE TABLE notas (codigo INT PRIMARY KEY, texto VARCHAR(60)) ON n2;
    CREATE INDEX por_monto ON ventas (monto) USING BPLUS;
    DROP TABLE ventas;
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

from pydantic import ValidationError

from engine.distributed.catalog import DistributedCatalog
from engine.distributed.contracts import INT32_MAX, INT32_MIN, DistributedTable
from engine.parser.ast import (
    CreateIndexStatement,
    CreateTableStatement,
    DropTableStatement,
    IndexKind,
    NodePlacement,
    RangeDistribution,
    TextLanguage,
    VectorMetric,
)
from engine.parser.errors import SQLSemanticError, SQLUnsupportedError
from engine.parser.semantic import bind_create_index, bind_create_table
from engine.parser.span import Span

DdlStatement = CreateTableStatement | CreateIndexStatement | DropTableStatement

_FRAGMENT_ID = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")

_SQL_INDEX = {
    "bplus_unclustered": "BPLUS",
    "extendible_hash": "HASH",
    "rtree": "RTREE",
    "inverted_index": "FULLTEXT",
    "ivf": "IVF",
    "hnsw": "HNSW",
}


def _unsupported(message: str, span: Span, sql: str) -> SQLUnsupportedError:
    return SQLUnsupportedError(f"[UNSUPPORTED_DISTRIBUTED] {message}", span, sql)


def _semantic(message: str, span: Span, sql: str) -> SQLSemanticError:
    return SQLSemanticError(message, span, sql)


def apply_ddl(catalog: DistributedCatalog, statement: DdlStatement, sql: str) -> DistributedCatalog:
    """El catálogo que resulta de ``statement``, con ``catalog_version`` + 1.

    Lanza ``SQLError`` con la posición del problema; no modifica ``catalog``.
    """

    if isinstance(statement, CreateTableStatement):
        tables = (*catalog.tables, _new_table(catalog, statement, sql))
    elif isinstance(statement, CreateIndexStatement):
        table = _existing(catalog, statement.table.name, statement.table.span, sql)
        updated = _with_index(table, statement, sql)
        tables = tuple(updated if t.name == table.name else t for t in catalog.tables)
    else:
        table = _existing(catalog, statement.table.name, statement.table.span, sql)
        tables = tuple(t for t in catalog.tables if t.name != table.name)
    return _validated(
        {
            **catalog.model_dump(mode="json"),
            "catalog_version": catalog.catalog_version + 1,
            "tables": [t.model_dump(mode="json") for t in tables],
        },
        statement.span,
        sql,
    )


def _validated(data: dict, span: Span, sql: str) -> DistributedCatalog:
    # Las reglas de abajo dan mensajes con posición; esta es la red final: el
    # catálogo nuevo cumple el mismo contrato que uno escrito a mano.
    try:
        return DistributedCatalog.model_validate(data)
    except ValidationError as error:
        detail = "; ".join(e["msg"].removeprefix("Value error, ") for e in error.errors())
        raise _semantic(
            f"el catálogo distribuido resultante no es válido: {detail}", span, sql
        ) from error


def _existing(catalog: DistributedCatalog, name: str, span: Span, sql: str) -> DistributedTable:
    try:
        return catalog.table(name)
    except KeyError:
        raise _semantic(f"la tabla {name!r} no existe", span, sql) from None


def _node(catalog: DistributedCatalog, identifier, sql: str) -> str:
    known = [n.node_id for n in catalog.nodes]
    if identifier.name not in known:
        raise _semantic(
            f"el nodo {identifier.name!r} no existe; los nodos son {', '.join(known)}",
            identifier.span,
            sql,
        )
    return identifier.name


def _fragment_id(catalog: DistributedCatalog, identifier, taken: set[str], sql: str) -> str:
    name = identifier.name
    if _FRAGMENT_ID.match(name) is None:
        raise _semantic(
            f"el nombre de fragmento {name!r} debe ir en minúsculas y empezar con una letra",
            identifier.span,
            sql,
        )
    if name in taken or any(f.fragment_id == name for t in catalog.tables for f in t.fragments):
        raise _semantic(f"ya existe un fragmento llamado {name!r}", identifier.span, sql)
    taken.add(name)
    return name


def _new_table(
    catalog: DistributedCatalog, statement: CreateTableStatement, sql: str
) -> DistributedTable:
    name = statement.table.name
    if any(t.name == name for t in catalog.tables):
        raise _semantic(f"la tabla {name!r} ya existe", statement.table.span, sql)
    distribution = statement.distribution
    if distribution is None:
        raise _unsupported(
            "en el coordinador CREATE TABLE debe decir dónde va la tabla: "
            "ON nodo o PARTITION BY RANGE (...)",
            statement.span,
            sql,
        )
    bound = bind_create_table(replace(statement, distribution=None), sql)
    columns = [
        {"name": c.name, "type": c.data_type.value, "size": c.length} for c in bound.schema.columns
    ]
    key = bound.schema.columns[bound.schema.key_column]
    storage = bound.storage.value.lower()

    if isinstance(distribution, NodePlacement):
        mode = "single"
        fragment = name.lower() if _FRAGMENT_ID.match(name.lower()) else None
        if fragment is None or any(
            f.fragment_id == fragment for t in catalog.tables for f in t.fragments
        ):
            raise _semantic(
                f"no se puede usar {name!r} como nombre de su fragmento", statement.table.span, sql
            )
        fragments = [
            {
                "fragment_id": fragment,
                "node_id": _node(catalog, distribution.node, sql),
                "lower": None,
                "upper": None,
            }
        ]
    else:
        mode = "range"
        fragments = _range_fragments(catalog, distribution, key, storage, sql)

    data = {
        "name": name,
        "mode": mode,
        "storage": storage,
        "primary_key": key.name,
        "columns": columns,
        "indexes": [],
        "fragments": fragments,
    }
    try:
        return DistributedTable.model_validate(data)
    except ValidationError as error:
        detail = "; ".join(e["msg"].removeprefix("Value error, ") for e in error.errors())
        raise _semantic(
            f"la tabla distribuida no es válida: {detail}", statement.span, sql
        ) from error


def _range_fragments(
    catalog: DistributedCatalog,
    distribution: RangeDistribution,
    key,
    storage: str,
    sql: str,
) -> list[dict]:
    column = distribution.column
    if column.name != key.name:
        raise _unsupported(
            f"se particiona por la clave primaria ({key.name}), no por {column.name}",
            column.span,
            sql,
        )
    if key.data_type.value != "INT":
        raise _unsupported("PARTITION BY RANGE requiere una clave primaria INT", column.span, sql)
    if storage != "heap":
        raise _unsupported("una tabla particionada se guarda como HEAP", distribution.span, sql)
    partitions = distribution.partitions
    if len(partitions) < 2:
        raise _semantic(
            "PARTITION BY RANGE requiere al menos dos particiones", distribution.span, sql
        )

    fragments: list[dict] = []
    taken: set[str] = set()
    nodes: dict[str, str] = {}
    lower = None
    for index, partition in enumerate(partitions):
        last = index == len(partitions) - 1
        fragment_id = _fragment_id(catalog, partition.name, taken, sql)
        node_id = _node(catalog, partition.node, sql)
        if node_id in nodes:
            raise _semantic(
                f"cada nodo guarda a lo sumo una partición de la tabla; {node_id} ya tiene "
                f"{nodes[node_id]}",
                partition.node.span,
                sql,
            )
        nodes[node_id] = fragment_id
        upper = partition.upper
        if upper is None and not last:
            raise _semantic("solo la última partición puede llegar a MAXVALUE", partition.span, sql)
        if upper is not None:
            if last:
                raise _semantic(
                    "la última partición debe ser VALUES LESS THAN (MAXVALUE) para cubrir "
                    "todas las claves",
                    upper.span,
                    sql,
                )
            if not INT32_MIN < upper.value <= INT32_MAX:
                raise _semantic(
                    f"el límite debe estar entre {INT32_MIN + 1} y {INT32_MAX}", upper.span, sql
                )
            if lower is not None and upper.value <= lower:
                raise _semantic(
                    f"los límites deben crecer: {upper.value} no es mayor que {lower}",
                    upper.span,
                    sql,
                )
        fragments.append(
            {
                "fragment_id": fragment_id,
                "node_id": node_id,
                "lower": lower,
                "upper": None if upper is None else upper.value,
            }
        )
        lower = None if upper is None else upper.value
    return fragments


def _with_index(
    table: DistributedTable, statement: CreateIndexStatement, sql: str
) -> DistributedTable:
    bound = bind_create_index(statement, table.bound_schema, sql)
    if any(i.name == bound.index_name for i in table.indexes):
        raise _semantic(
            f"el índice {bound.index_name!r} ya existe en {table.name}", statement.index.span, sql
        )
    if statement.metric not in (None, VectorMetric.COSINE):
        raise _unsupported(
            "en modo distribuido HNSW usa la métrica coseno; quita WITH METRIC",
            statement.span,
            sql,
        )
    if statement.language not in (None, TextLanguage.SPANISH):
        raise _unsupported(
            "en modo distribuido FULLTEXT usa el idioma por omisión; quita WITH LANGUAGE",
            statement.span,
            sql,
        )
    if table.storage != "heap":
        raise _unsupported(
            "los índices secundarios distribuidos requieren una tabla HEAP",
            statement.table.span,
            sql,
        )
    if table.mode == "range" and statement.kind is IndexKind.INVERTED_INDEX:
        raise _unsupported(
            "FULLTEXT no se admite en una tabla particionada: el puntaje de cada nodo "
            "usa estadísticas de su fragmento",
            statement.span,
            sql,
        )
    structure = statement.kind.value.lower()
    index = {
        "name": bound.index_name,
        "column": bound.column_name,
        "structure": structure,
        "supports_range": structure == "bplus_unclustered",
    }
    data = table.model_dump(mode="json")
    data["indexes"] = [*data["indexes"], index]
    try:
        return DistributedTable.model_validate(data)
    except ValidationError as error:
        detail = "; ".join(e["msg"].removeprefix("Value error, ") for e in error.errors())
        raise _semantic(f"el índice no es válido: {detail}", statement.span, sql) from error


# -- lo que cada nodo aplica ---------------------------------------------------


@dataclass(frozen=True)
class NodeChanges:
    """Cambios físicos de un nodo al pasar de un catálogo a otro.

    Las altas van primero porque se pueden deshacer; las bajas, al final.
    """

    create_tables: tuple[DistributedTable, ...]
    create_indexes: tuple[tuple[str, str], ...]
    drop_indexes: tuple[tuple[str, str], ...]
    drop_tables: tuple[str, ...]

    @property
    def empty(self) -> bool:
        return not (
            self.create_tables or self.create_indexes or self.drop_indexes or self.drop_tables
        )


class CatalogChangeError(ValueError):
    """El catálogo nuevo cambia algo que DDL v1 no sabe aplicar."""


def changes_for_node(old: DistributedCatalog, new: DistributedCatalog, node_id: str) -> NodeChanges:
    """Qué tiene que crear o borrar ``node_id`` para pasar de ``old`` a ``new``.

    Solo se admite agregar o quitar tablas y agregar o quitar índices. Cambiar
    columnas, rangos o nodos de una tabla existente sería mover datos, y eso
    v1 no lo hace.
    """

    if new.protocol_version != old.protocol_version:
        raise CatalogChangeError("no se puede cambiar protocol_version")
    if new.catalog_version <= old.catalog_version:
        raise CatalogChangeError(
            f"catalog_version {new.catalog_version} no es mayor que {old.catalog_version}"
        )
    if new.nodes != old.nodes:
        raise CatalogChangeError("no se pueden agregar, quitar ni mover nodos")
    before = {t.name: t for t in old.tables}
    after = {t.name: t for t in new.tables}
    for name in before.keys() & after.keys():
        if before[name].model_copy(update={"indexes": ()}) != after[name].model_copy(
            update={"indexes": ()}
        ):
            raise CatalogChangeError(f"la tabla {name} cambió más que sus índices")

    def local(table: DistributedTable) -> bool:
        return any(f.node_id == node_id for f in table.fragments)

    create_tables = tuple(t for n, t in after.items() if n not in before and local(t))
    drop_tables = tuple(n for n, t in before.items() if n not in after and local(t))
    create_indexes = []
    drop_indexes = []
    for name in before.keys() & after.keys():
        if not local(after[name]):
            continue
        old_indexes = {i.name: i for i in before[name].indexes}
        new_indexes = {i.name: i for i in after[name].indexes}
        for index in sorted(new_indexes.keys() - old_indexes.keys()):
            create_indexes.append((name, index))
        for index in sorted(old_indexes.keys() - new_indexes.keys()):
            drop_indexes.append((name, index))
        for index in old_indexes.keys() & new_indexes.keys():
            if old_indexes[index] != new_indexes[index]:
                raise CatalogChangeError(f"el índice {index} de {name} cambió de definición")
    # Una tabla nueva llega con sus índices: también se crean.
    for table in create_tables:
        create_indexes.extend((table.name, i.name) for i in table.indexes)
    return NodeChanges(create_tables, tuple(create_indexes), tuple(drop_indexes), drop_tables)


def create_table_sql(table: DistributedTable) -> str:
    """El ``CREATE TABLE`` local con que un nodo crea su fragmento."""

    def column(c) -> str:
        size = "" if c.size is None else f"({c.size})"
        key = " PRIMARY KEY" if c.name == table.primary_key else ""
        return f"{c.name} {c.type}{size}{key}"

    columns = ", ".join(column(c) for c in table.columns)
    return f"CREATE TABLE {table.name} ({columns}) USING {table.storage.upper()}"


def create_index_sql(table: DistributedTable, index_name: str) -> str:
    """El ``CREATE INDEX`` local de un índice declarado en el catálogo."""

    index = next(i for i in table.indexes if i.name == index_name)
    return (
        f"CREATE INDEX {index.name} ON {table.name} ({index.column}) "
        f"USING {_SQL_INDEX[index.structure]}"
    )
