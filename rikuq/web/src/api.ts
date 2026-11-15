export async function health(base = "/api", request: typeof fetch = fetch): Promise<boolean> {
  const response = await request(`${base}/health`);
  if (!response.ok) throw new Error("El servidor de Rikuq no responde.");
  const body = await response.json();
  return body.status === "ok" && body.app === "rikuq";
}

export const MAX_IMAGEN = 8 * 1024 * 1024;
export const API = import.meta.env?.VITE_RIKUQ_API_URL || "/api";

export interface Rostro {
  persona: string | null;
  persona_id: number | null;
  foto: string | null;
  distancia: number | null;
  confianza: number;
  caja: [number, number, number, number];
  ms_motor: number;
}

export interface Identificacion extends Rostro {
  rostros: Rostro[];
  ancho: number;
  alto: number;
  ms_total: number;
  /** Aditivo: un servidor anterior puede no informar el umbral. */
  umbral?: number;
}

export class ApiError extends Error {
  codigo: string;
  constructor(mensaje: string, codigo: string) {
    super(mensaje);
    this.codigo = codigo;
  }
}

export function validarImagen(archivo: Pick<File, "size" | "type">): string | null {
  if (!archivo.size) return "La imagen está vacía. Elige otro archivo.";
  if (archivo.size > MAX_IMAGEN) return "La imagen supera 8 MiB. Reduce su tamaño antes de subirla.";
  if (!["image/jpeg", "image/png", "image/webp"].includes(archivo.type)) return "Elige una imagen JPEG, PNG o WebP.";
  return null;
}

export async function identificar(
  imagen: Blob, signal: AbortSignal, base = API, request: typeof fetch = fetch,
): Promise<Identificacion> {
  const error = validarImagen(imagen);
  if (error) throw new ApiError(error, "imagen_invalida");
  const form = new FormData();
  form.append("imagen", imagen, "consulta");
  const response = await request(`${base}/identificar`, { method: "POST", body: form, signal });
  const body = await response.json().catch(() => null);
  if (!response.ok) throw new ApiError(
    body?.error || (typeof body?.detail === "string" ? body.detail : "El servidor no pudo analizar la imagen."),
    body?.codigo || "error_servidor",
  );
  if (!body || !Array.isArray(body.rostros) || !(body.ancho > 0) || !(body.alto > 0)) {
    throw new ApiError("El servidor devolvió una respuesta incompleta.", "error_servidor");
  }
  return body as Identificacion;
}

export function fotoUrl(ruta: string, base = API): string {
  // Solo la ruta opaca del API: nunca URLs de terceros recibidas en un resultado.
  return /^\/personas\/\d+\/foto$/.test(ruta) ? `${base}${ruta}` : "";
}

export function mensajeError(error: unknown): string {
  return error instanceof ApiError ? error.message : "No se pudo conectar con Rikuq. Revisa el servidor e intenta otra vez.";
}
