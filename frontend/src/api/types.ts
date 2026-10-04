/**
 * Espejo TypeScript del contrato que expone el motor.
 *
 * `Plan`, `Step` y `Stats` replican ADR 0002 (docs/adr/0002-plan-de-ejecucion.md)
 * tal como los serializa `Plan.to_dict()` en engine/planner/plan.py. Si cambian
 * ahi, cambian aqui en el mismo PR.
 */

/** Operaciones de un plan. Los hijos se ejecutan antes que el padre. */
export type Op =
  | "scan"
  | "search"
  | "range_search"
  | "index_search"
  | "index_range"
  | "radius_search"
  | "knn_search"
  | "fetch"
  | "filter"
  | "project"
  | "sort"
  | "group"
  | "join"
  | "limit"
  | "insert"
  | "remove"
  | "update";

/** Las cinco primeras son las constantes `kind::` del core. */
export type Structure =
  | "heap"
  | "sequential"
  | "bplus_clustered"
  | "bplus_unclustered"
  | "extendible_hash"
  | "rtree"
  | "external_sort"
  | "external_hash"
  | "memory";

/** Organizaciones que puede tener una tabla fisica. */
export type TableStructure = Extract<
  Structure,
  "heap" | "sequential" | "bplus_clustered"
>;

/** Espejo de `OpStats` del core. */
export interface Stats {
  pages_read: number;
  pages_written: number;
  records_examined: number;
  records_returned: number;
}

/** Un nodo del plan. `stats` y `time_ms` son propios, sin incluir hijos. */
export interface Step {
  op: Op;
  structure: Structure;
  table: string | null;
  column: string | null;
  detail: string;
  stats: Stats;
  time_ms: number;
  children: Step[];
}

export interface Plan {
  query: string;
  time_ms: number;
  totals: Stats;
  root: Step;
}

export interface PointValue {
  latitude: number;
  longitude: number;
}

export interface RadiusContext {
  kind: "radius";
  /** Solo una fuente simple, resuelta sin ambiguedad por el ejecutor. */
  table: string | null;
  column: string;
  center: PointValue;
  radius: number;
  metric: "HAVERSINE" | "EUCLIDEAN";
  unit: "meters" | "degrees";
  operator: "<" | "<=";
}

/** Nuevos contextos se incorporaran cuando el motor los exponga realmente. */
export type SpatialContext = RadiusContext;

export type CellValue = string | number | boolean | PointValue | null;

/** Espejo de QueryResult; los tipos vienen del esquema, incluso sin filas. */
export interface QueryResult {
  columns: string[];
  /** Un tipo por columna, en el mismo orden que `columns`. */
  column_types: DataType[];
  rows: CellValue[][];
  affected_rows: number;
  plan: Plan | null;
  spatial_context?: SpatialContext | null;
  is_explain?: boolean;
}

export type DataType =
  | "INT"
  | "VARCHAR"
  | "DOUBLE"
  | "BOOL"
  | "DATE"
  | "POINT";

export interface ColumnInfo {
  name: string;
  type: DataType;
  /** Solo lo declara VARCHAR; el resto tiene tamano fijo por tipo. */
  size: number | null;
  is_primary_key: boolean;
}

export interface IndexInfo {
  name: string;
  /** Nombre de la columna, no su posicion: ver la nota de `TableInfo`. */
  column: string;
  structure: Extract<
    Structure,
    "bplus_unclustered" | "extendible_hash" | "rtree"
  >;
  supports_range: boolean;
}

/**
 * Lo que `GET /tables` debe devolver por cada tabla del catalogo.
 *
 * No es el espejo de una sola estructura del motor, sino lo que el Panel de
 * Archivos necesita mostrar, y la API lo compone de tres fuentes:
 *
 * | campo | de donde sale | traduccion que hace la API |
 * |---|---|---|
 * | `storage`, `indexes` | `TableMetadata` del planner | `IndexMetadata.column` es la POSICION de la columna; aqui viaja su nombre |
 * | `columns` | `Schema` del core | `Column.length` vale 0 fuera de VARCHAR; aqui es `size: null` |
 * | `is_primary_key` | `Schema.key_column` | alla es una posicion; aqui un booleano por columna |
 * | `record_count` | `TableFile.size()` | sin cambios |
 *
 * Se resuelve en la API y no aqui porque el frontend no deberia cargar con
 * mapear posiciones a nombres para dibujar una lista.
 */
export interface TableInfo {
  name: string;
  storage: TableStructure;
  columns: ColumnInfo[];
  indexes: IndexInfo[];
  record_count: number;
}

/** Una fila del CSV que no entro. Espejo de `LoadRowError` de la API. */
export interface LoadRowError {
  /** Linea del archivo donde empieza la fila, contando la cabecera como 1. */
  line: number;
  error: string;
}

/**
 * Lo que responde `POST /tables/{tabla}/load`.
 *
 * Espejo de `LoadResponse` en `engine/api/schemas.py`. `failed` cuenta TODAS
 * las filas que no entraron; `errors` detalla solo las primeras y
 * `errors_truncated` avisa de que hay mas, para que un archivo con 100 000
 * filas malas no devuelva 100 000 mensajes.
 */
export interface LoadResult {
  table: string;
  /** Con que se leyo el archivo. cp1252 es lo que escribe Excel en Windows. */
  encoding: "utf-8" | "cp1252";
  inserted: number;
  failed: number;
  errors: LoadRowError[];
  errors_truncated: boolean;
}

/** Familia del error, como las separa `engine/parser/errors.py`. */
export type ErrorKind = "lex" | "parse" | "semantic" | "unsupported";

/**
 * Error del motor con la ubicacion que reporta `Span`.
 *
 * Lineas y columnas se cuentan desde 1 y `end_column` es exclusivo, igual que
 * en `engine/parser/span.py`. Es la misma convencion que usa Monaco, asi que
 * el rango viaja al editor sin ajustes.
 */
export interface QueryError {
  error: string;
  kind: ErrorKind | null;
  line: number | null;
  column: number | null;
  end_line: number | null;
  end_column: number | null;
}
