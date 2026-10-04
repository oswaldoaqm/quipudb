import type {
  CellValue, PointValue, PolygonContext, QueryResult, RadiusContext, TableInfo,
} from "@/api/types";

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

export type SpatialView =
  | { kind: "circle"; center: [number, number]; radius: number }
  | { kind: "polygon"; vertices: [number, number][] }
  | { kind: "points"; positions: [number, number][] }
  | { kind: "empty" };

/** Admite solo el contexto actualmente soportado y unidades coherentes. */
export function radiusContext(result: QueryResult | null): RadiusContext | null {
  const context = result?.spatial_context;
  if (!context || result?.is_explain || context.kind !== "radius" ||
      !isPointValue(context.center) || !Number.isFinite(context.radius) || context.radius < 0 ||
      (context.operator !== "<" && context.operator !== "<=") ||
      !((context.metric === "HAVERSINE" && context.unit === "meters") ||
        (context.metric === "EUCLIDEAN" && context.unit === "degrees"))) return null;
  return context;
}

/** Un poligono valido: al menos tres vertices con coordenadas geograficas validas. */
export function polygonContext(result: QueryResult | null): PolygonContext | null {
  const context = result?.spatial_context;
  if (!context || result?.is_explain || context.kind !== "polygon" ||
      !Array.isArray(context.vertices) || context.vertices.length < 3 ||
      !context.vertices.every(isPointValue)) return null;
  return context;
}

/** El contexto espacial valido de la consulta, sea de radio o de poligono. */
export function searchContext(result: QueryResult | null): RadiusContext | PolygonContext | null {
  return radiusContext(result) ?? polygonContext(result);
}

/**
 * Si `point` cae dentro del poligono o sobre su borde. Traduccion del
 * `contains_point` del core (rtree.cpp), con x = longitud e y = latitud, para que
 * el simulador decida el borde igual que el motor.
 */
export function containsPoint(vertices: readonly PointValue[], point: PointValue): boolean {
  if (vertices.length < 3) return false;
  const px = point.longitude;
  const py = point.latitude;
  const onSegment = (a: PointValue, b: PointValue) => {
    const scale = Math.max(Math.abs(a.longitude), Math.abs(a.latitude), Math.abs(b.longitude),
      Math.abs(b.latitude), Math.abs(px), Math.abs(py), 1);
    const cross = (b.longitude - a.longitude) * (py - a.latitude) - (b.latitude - a.latitude) * (px - a.longitude);
    if (Math.abs(cross) > scale * scale * 1e-12) return false;
    const slack = scale * 1e-12;
    return px >= Math.min(a.longitude, b.longitude) - slack && px <= Math.max(a.longitude, b.longitude) + slack &&
      py >= Math.min(a.latitude, b.latitude) - slack && py <= Math.max(a.latitude, b.latitude) + slack;
  };
  for (let i = 0, j = vertices.length - 1; i < vertices.length; j = i++) {
    if (onSegment(vertices[j], vertices[i])) return true;
  }
  let inside = false;
  for (let i = 0, j = vertices.length - 1; i < vertices.length; j = i++) {
    const a = vertices[i];
    const b = vertices[j];
    if ((a.latitude > py) !== (b.latitude > py) &&
        px < (b.longitude - a.longitude) * (py - a.latitude) / (b.latitude - a.latitude) + a.longitude) {
      inside = !inside;
    }
  }
  return inside;
}

/** La capa base deliberadamente no participa de esta politica de encuadre. */
export function spatialView(
  context: RadiusContext | PolygonContext | null, points: SpatialPoint[],
): SpatialView {
  if (context?.kind === "polygon") {
    return { kind: "polygon", vertices: context.vertices.map(v => [v.latitude, v.longitude]) };
  }
  if (context) {
    const center: [number, number] = [context.center.latitude, context.center.longitude];
    if (context.radius === 0) return { kind: "points", positions: [center] };
    if (context.metric === "HAVERSINE") return { kind: "circle", center, radius: context.radius };
    return { kind: "points", positions: [center, ...points.map(p => p.position)] };
  }
  return points.length ? { kind: "points", positions: points.map(p => p.position) } : { kind: "empty" };
}

/** Estados excluyentes: nunca conserva los marcadores de otra ejecucion. */
export function mapMessage(result: QueryResult | null, loading: boolean, error: boolean): string | null {
  if (error) return "La consulta no se ejecutó. Revisa el error junto al editor.";
  if (loading) return "Ejecutando consulta…";
  if (!result) return "Ejecuta una consulta con una columna POINT para ver sus puntos.";
  if (result.is_explain) return "EXPLAIN muestra el plan; no devuelve puntos para el mapa.";
  if (searchContext(result)) return null;
  if (!result.rows.length) return "0 coincidencias. La consulta no devolvió resultados.";
  const spatial = extractSpatialPoints(result);
  if (!spatial.pointColumns.length) return "El resultado no contiene columnas POINT. Incluye una columna de coordenadas en el SELECT.";
  if (!spatial.points.length) return "No hay coordenadas válidas. Se requiere latitud entre −90 y 90 y longitud entre −180 y 180, con valores numéricos finitos.";
  return null;
}

function columnPosition(result: QueryResult, table: TableInfo, name: string): number {
  const matches = result.columns.flatMap((column, index) =>
    column === name || column === `${table.name}.${name}` ? [index] : []);
  return matches.length === 1 ? matches[0] : -1;
}

/** Solo la PK declarada y tipada identifica un registro, nunca sus coordenadas. */
export function pointIdentity(point: SpatialPoint, result: QueryResult, table: TableInfo): string | null {
  const keys = table.columns.filter(c => c.is_primary_key);
  if (keys.length !== 1) return null;
  const key = keys[0];
  const index = columnPosition(result, table, key.name);
  if (index < 0 || result.column_types[index] !== key.type) return null;
  const value = point.row[index];
  const validKey = ((key.type === "INT" || key.type === "DOUBLE") && typeof value === "number" && Number.isFinite(value)) ||
    ((key.type === "VARCHAR" || key.type === "DATE") && typeof value === "string") ||
    (key.type === "BOOL" && typeof value === "boolean");
  if (!validKey) return null;
  const column = table.columns.find(c => c.type === "POINT" &&
    (point.columnName === c.name || point.columnName === `${table.name}.${c.name}`));
  return column ? JSON.stringify([table.name, key.type, value, column.name]) : null;
}

export function basePointsOutsideResults(
  base: QueryResult | null, result: QueryResult | null, table: TableInfo | null,
): { points: SpatialPoint[]; hasIdentity: boolean } {
  const points = extractSpatialPoints(base).points;
  if (!base || !result || !table || searchContext(result)?.table !== table.name) {
    return { points: [], hasIdentity: false };
  }
  const resultPoints = extractSpatialPoints(result).points;
  const identities = resultPoints.map(p => pointIdentity(p, result, table));
  const hasIdentity = identities.every(id => id !== null);
  const selected = new Set(identities.filter((id): id is string => id !== null));
  return {
    points: points.filter(p => !selected.has(pointIdentity(p, base, table) ?? "")),
    hasIdentity,
  };
}
