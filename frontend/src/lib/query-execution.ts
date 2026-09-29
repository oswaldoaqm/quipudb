import type { MotorError } from "@/api/errors";
import type { QueryResult } from "@/api/types";

export interface QueryExecution {
  id: number;
  sql: string;
  result: QueryResult | null;
  error: MotorError | null;
  pending: boolean;
}
export type ExecutionEvent =
  | { type: "begin"; id: number; sql: string }
  | { type: "complete"; id: number; result: QueryResult }
  | { type: "fail"; id: number; error: MotorError };
export const initialExecution: QueryExecution = { id: 0, sql: "", result: null, error: null, pending: false };

/** Resultado y SQL ejecutado permanecen juntos aunque cambie el editor. */
export function executionReducer(state: QueryExecution, event: ExecutionEvent): QueryExecution {
  if (event.type === "begin") return { id: event.id, sql: event.sql, result: null, error: null, pending: true };
  if (event.id !== state.id) return state;
  if (event.type === "complete") return { ...state, result: event.result, pending: false };
  return { ...state, result: null, error: event.error, pending: false };
}
