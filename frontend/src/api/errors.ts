/**
 * Error del motor tal como lo ve la interfaz.
 *
 * Vive aparte de `client.ts` porque tambien lo lanza `mock.ts`, y que el
 * cliente lo importara del mock (o al reves) seria un ciclo.
 */

import type { ErrorKind, QueryError } from "@/api/types";

export class MotorError extends Error {
  readonly kind: ErrorKind | null;
  readonly line: number | null;
  readonly column: number | null;
  readonly endLine: number | null;
  readonly endColumn: number | null;

  constructor(detalle: QueryError) {
    super(detalle.error);
    this.name = "MotorError";
    this.kind = detalle.kind;
    this.line = detalle.line;
    this.column = detalle.column;
    this.endLine = detalle.end_line;
    this.endColumn = detalle.end_column;
  }

  /** Un error de red o del servidor no apunta a ningun punto de la consulta. */
  static sinUbicacion(mensaje: string): MotorError {
    return new MotorError({
      error: mensaje,
      kind: null,
      line: null,
      column: null,
      end_line: null,
      end_column: null,
    });
  }

  /** True cuando el error senala un punto concreto del texto de la consulta. */
  get tieneUbicacion(): boolean {
    return this.line !== null && this.column !== null;
  }
}

/** Como nombrar cada familia de error para quien lee, no para quien programa. */
export const ETIQUETA_ERROR: Record<ErrorKind, string> = {
  lex: "Caracter no reconocido",
  parse: "Error de sintaxis",
  semantic: "Error de validacion",
  unsupported: "No soportado",
};
