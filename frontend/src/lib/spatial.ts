import type { CellValue, PointValue, QueryResult } from "@/api/types";

export interface SpatialPoint {
  /** Identidad dentro del resultado; dos filas pueden compartir coordenadas. */
  key: string;
  rowIndex: number;
  columnIndex: number;
  columnName: string;
  row: readonly CellValue[];
  position: [latitude: number, longitude: number];
}

export interface SpatialResult {
  pointColumns: number[];
  points: SpatialPoint[];
  invalidCount: number;
}

/** La API se valida en runtime: los tipos de TypeScript no validan el JSON. */
export function isPointValue(value: unknown): value is PointValue {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return false;
  }
  const { latitude, longitude } = value as Partial<PointValue>;
  return (
    typeof latitude === "number" &&
    typeof longitude === "number" &&
    Number.isFinite(latitude) &&
    Number.isFinite(longitude) &&
    latitude >= -90 && latitude <= 90 &&
    longitude >= -180 && longitude <= 180
  );
}

/** Solo adapta las filas recibidas; no consulta ni calcula coincidencias. */
export function extractSpatialPoints(result: QueryResult | null): SpatialResult {
  const pointColumns: number[] = [];
  const points: SpatialPoint[] = [];
  let invalidCount = 0;
  if (!result) return { pointColumns, points, invalidCount };

  result.column_types.forEach((type, index) => {
    if (type === "POINT") pointColumns.push(index);
  });
  result.rows.forEach((row, rowIndex) => {
    pointColumns.forEach((columnIndex) => {
      const value = row[columnIndex];
      if (!isPointValue(value)) {
        invalidCount += 1;
        return;
      }
      points.push({
        key: `${rowIndex}:${columnIndex}`,
        rowIndex,
        columnIndex,
        columnName: result.columns[columnIndex] ?? `Columna ${columnIndex + 1}`,
        row,
        position: [value.latitude, value.longitude],
      });
    });
  });
  return { pointColumns, points, invalidCount };
}
