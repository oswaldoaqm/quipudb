/**
 * Unico punto por el que la interfaz habla con el motor.
 *
 * Los paneles llaman siempre a estas funciones, sin saber si detras hay una
 * API o los datos falsos de `mock.ts`. Cuando `engine/api/` exista, basta con
 * poner VITE_USE_MOCK=false: ningun componente cambia.
 */

import { mockExecuteQuery, mockListTables } from "@/api/mock";
import type { QueryError, QueryResult, TableInfo } from "@/api/types";

const URL_API = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

/** Sin motor levantado se usan datos falsos; es el modo por defecto. */
export const USA_DATOS_FALSOS = import.meta.env.VITE_USE_MOCK !== "false";

/** Error del motor. Los del parser traen la ubicacion que reporta `Span`. */
export class MotorError extends Error {
  readonly line: number | null;
  readonly column: number | null;

  constructor(mensaje: string, line: number | null, column: number | null) {
    super(mensaje);
    this.name = "MotorError";
    this.line = line;
    this.column = column;
  }
}

async function pedir<T>(ruta: string, init?: RequestInit): Promise<T> {
  let respuesta: Response;
  try {
    respuesta = await fetch(`${URL_API}${ruta}`, {
      headers: { "Content-Type": "application/json" },
      ...init,
    });
  } catch {
    throw new MotorError(
      `No se pudo contactar al motor en ${URL_API}. Levantalo, o usa VITE_USE_MOCK=true`,
      null,
      null,
    );
  }

  if (!respuesta.ok) {
    const detalle = (await respuesta.json().catch(() => null)) as QueryError | null;
    throw new MotorError(
      detalle?.error ?? `El motor respondio ${respuesta.status}`,
      detalle?.line ?? null,
      detalle?.column ?? null,
    );
  }

  return (await respuesta.json()) as T;
}

/** Ejecuta una sentencia SQL y devuelve filas y plan de ejecucion. */
export function executeQuery(sql: string): Promise<QueryResult> {
  if (USA_DATOS_FALSOS) {
    return mockExecuteQuery(sql);
  }
  return pedir<QueryResult>("/query", {
    method: "POST",
    body: JSON.stringify({ sql }),
  });
}

/** Lista las tablas del catalogo con su organizacion, columnas e indices. */
export function listTables(): Promise<TableInfo[]> {
  if (USA_DATOS_FALSOS) {
    return mockListTables();
  }
  return pedir<TableInfo[]>("/tables");
}
