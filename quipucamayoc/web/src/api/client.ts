/**
 * Unico punto por el que la interfaz habla con el servidor de Quipucamayoc.
 *
 * La interfaz nunca llama al motor ni a Gemini: la clave del LLM vive solo en
 * el servidor (ADR 0010).
 */

import type { Health } from "@/api/types";

export const URL_SERVIDOR = import.meta.env.VITE_SERVER_URL ?? "http://localhost:8001";

export class ServidorError extends Error {}

async function pedir<T>(ruta: string, init?: RequestInit): Promise<T> {
  let respuesta: Response;
  try {
    respuesta = await fetch(`${URL_SERVIDOR}${ruta}`, {
      headers: { "Content-Type": "application/json" },
      ...init,
    });
  } catch {
    throw new ServidorError(
      `No se pudo contactar al servidor en ${URL_SERVIDOR}. Levantalo con uvicorn app.main:app --port 8001`,
    );
  }
  if (!respuesta.ok) {
    throw new ServidorError(`El servidor respondio ${respuesta.status}`);
  }
  return (await respuesta.json()) as T;
}

export function obtenerSalud(): Promise<Health> {
  return pedir<Health>("/health");
}
