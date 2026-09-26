import type { QueryError } from "@/api/types";

export type Ubicacion = Pick<
  QueryError,
  "line" | "column" | "end_line" | "end_column"
>;

/** Ubicacion 1-based de un tramo, con la convencion de `Span` del parser. */
export function ubicarEn(
  sql: string,
  indice: number,
  largo: number,
): Ubicacion {
  const lineas = sql.slice(0, Math.max(0, indice)).split(/\r\n|\r|\n/);
  const line = lineas.length;
  const column = (lineas[lineas.length - 1]?.length ?? 0) + 1;
  return { line, column, end_line: line, end_column: column + largo };
}
