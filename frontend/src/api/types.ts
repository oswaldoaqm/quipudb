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

export type CellValue = string | number | boolean | null;

/** Espejo de `QueryResult` de engine/executor/result.py. */
export interface QueryResult {
  columns: string[];
  rows: CellValue[][];
  affected_rows: number;
  plan: Plan | null;
}

export type DataType = "INT" | "VARCHAR" | "DOUBLE" | "BOOL" | "DATE";

export interface ColumnInfo {
  name: string;
  type: DataType;
  /** Solo lo declara VARCHAR; el resto tiene tamano fijo por tipo. */
  size: number | null;
  is_primary_key: boolean;
}

export interface IndexInfo {
  name: string;
  column: string;
  structure: Extract<Structure, "bplus_unclustered" | "extendible_hash">;
  supports_range: boolean;
}

export interface TableInfo {
  name: string;
  storage: TableStructure;
  columns: ColumnInfo[];
  indexes: IndexInfo[];
  record_count: number;
}

/** Error del parser: trae la ubicacion que reporta `Span`. */
export interface QueryError {
  error: string;
  line: number | null;
  column: number | null;
}
