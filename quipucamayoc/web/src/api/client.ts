/**
 * Unico punto por el que la interfaz habla con el servidor de Quipucamayoc.
 *
 * La interfaz nunca llama al motor ni a Gemini: la clave del LLM vive solo en
 * el servidor (ADR 0010). Con VITE_USE_MOCK=true todo sale de mock.ts, para
 * trabajar la interfaz sin servidor, motor ni clave.
 */

import * as mock from "@/api/mock";
import type {
  Agregado,
  AskRequest,
  Evento,
  FiltrosPapers,
  Health,
  NuevoPaper,
  Origen,
  PapersResponse,
} from "@/api/types";

export const URL_SERVIDOR = import.meta.env.VITE_SERVER_URL ?? "http://127.0.0.1:8001";
export const USA_DATOS_FALSOS = import.meta.env.VITE_USE_MOCK === "true";

/** Un fallo con su origen: la interfaz muestra distinto al motor, a Gemini y al servidor. */
export class ServidorError extends Error {
  constructor(
    message: string,
    readonly origen: Origen | "servidor" = "servidor",
  ) {
    super(message);
  }
}

async function enviar(ruta: string, init?: RequestInit): Promise<Response> {
  try {
    return await fetch(`${URL_SERVIDOR}${ruta}`, init);
  } catch {
    throw new ServidorError(
      `No se pudo contactar al servidor en ${URL_SERVIDOR}. Levántalo con ` +
        "uvicorn app.main:app --port 8001, o usa VITE_USE_MOCK=true.",
    );
  }
}

async function fallo(respuesta: Response): Promise<ServidorError> {
  let cuerpo: { error?: string; origen?: Origen; detail?: unknown } = {};
  try {
    cuerpo = await respuesta.json();
  } catch {
    // Sin cuerpo JSON: queda el codigo.
  }
  const mensaje =
    cuerpo.error ??
    (typeof cuerpo.detail === "string" ? cuerpo.detail : `el servidor respondió ${respuesta.status}`);
  return new ServidorError(mensaje, cuerpo.origen ?? "servidor");
}

async function pedir<T>(ruta: string, init?: RequestInit): Promise<T> {
  const respuesta = await enviar(ruta, init);
  if (!respuesta.ok) throw await fallo(respuesta);
  return (await respuesta.json()) as T;
}

export function obtenerSalud(): Promise<Health> {
  return USA_DATOS_FALSOS ? mock.salud() : pedir<Health>("/health");
}

/**
 * Pregunta por POST /ask/stream y avisa cada etapa a medida que llega.
 * Resuelve cuando termina el flujo; un evento `error` tambien lo termina.
 */
export async function preguntar(
  peticion: AskRequest,
  alLlegar: (evento: Evento) => void,
  senal?: AbortSignal,
): Promise<void> {
  if (USA_DATOS_FALSOS) return mock.preguntar(peticion, alLlegar, senal);

  const respuesta = await enviar("/ask/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(peticion),
    signal: senal,
  });
  if (!respuesta.ok || !respuesta.body) throw await fallo(respuesta);

  // NDJSON: una linea por evento. Un trozo de red puede cortar una linea a la
  // mitad, asi que lo que sobra despues del ultimo salto espera al siguiente.
  const lector = respuesta.body.pipeThrough(new TextDecoderStream()).getReader();
  let pendiente = "";
  for (;;) {
    const { value, done } = await lector.read();
    if (done) break;
    pendiente += value;
    const lineas = pendiente.split("\n");
    pendiente = lineas.pop() ?? "";
    for (const linea of lineas) if (linea.trim()) alLlegar(JSON.parse(linea) as Evento);
  }
  if (pendiente.trim()) alLlegar(JSON.parse(pendiente) as Evento);
}

export function listarPapers(filtros: FiltrosPapers): Promise<PapersResponse> {
  if (USA_DATOS_FALSOS) return mock.papers(filtros);
  const params = new URLSearchParams();
  for (const [clave, valor] of Object.entries(filtros)) {
    if (valor !== undefined && valor !== "") params.set(clave, String(valor));
  }
  const consulta = params.toString();
  return pedir<PapersResponse>(`/papers${consulta ? `?${consulta}` : ""}`);
}

export function agregarPaper(nuevo: NuevoPaper): Promise<Agregado> {
  if (USA_DATOS_FALSOS) return mock.agregar(nuevo);
  // Sin Content-Type a mano: el navegador escribe el boundary del multipart.
  const formulario = new FormData();
  formulario.set("archivo", nuevo.archivo);
  formulario.set("titulo", nuevo.titulo);
  formulario.set("autores", nuevo.autores);
  formulario.set("anio", String(nuevo.anio));
  formulario.set("abstract", nuevo.abstract);
  return pedir<Agregado>("/papers", { method: "POST", body: formulario });
}
