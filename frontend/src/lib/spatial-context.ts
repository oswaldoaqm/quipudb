import type { QueryResult, TableInfo } from "@/api/types";

/** Solo identificadores del catalogo; nunca se interpreta ni modifica SQL del usuario. */
function identifier(name: string): string {
  if (!/^[a-zA-Z_][a-zA-Z0-9_]*$/.test(name)) {
    throw new Error("El nombre del catálogo no permite cargar puntos de contexto.");
  }
  return name;
}

export function baseCacheKey(table: TableInfo, revision: number): string {
  return JSON.stringify([table.name, table.columns, table.record_count, revision]);
}

/** Cache acotada a la ultima tabla. Comparte solicitudes en curso y descarta fallos. */
export function createSpatialBaseCache(execute: (sql: string) => Promise<QueryResult>) {
  let cached: { key: string; response: Promise<QueryResult> } | null = null;
  return {
    load(table: TableInfo, revision: number): Promise<QueryResult> {
      const key = baseCacheKey(table, revision);
      if (cached?.key === key) return cached.response;
      const columns = table.columns.filter(c => c.is_primary_key || c.type === "POINT");
      const sql = `SELECT ${columns.map(c => identifier(c.name)).join(", ")} FROM ${identifier(table.name)};`;
      const response = execute(sql).catch(error => {
        if (cached?.response === response) cached = null;
        throw error;
      });
      cached = { key, response };
      return response;
    },
  };
}
