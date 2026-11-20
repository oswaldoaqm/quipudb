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
  /** Aviso opcional del servidor sobre rostros pequenos. */
  fiable?: boolean;
  /**
   * Las fotos de la persona identificada que tambien pasan el umbral, de la
   * mas parecida a la menos (#344). Aditivo: un servidor anterior no lo envia.
   */
  coincidencias?: Coincidencia[];
}

export interface Coincidencia {
  rostro_id: number;
  foto: string;
  distancia: number;
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
  const body = await cuerpo(response, "El servidor no pudo analizar la imagen.");
  if (!body || !Array.isArray(body.rostros) || !(body.ancho > 0) || !(body.alto > 0)) {
    throw new ApiError("El servidor devolvió una respuesta incompleta.", "error_servidor");
  }
  return body as Identificacion;
}

/** El JSON de la respuesta, o un ApiError con el mensaje y el código del servidor. */
async function cuerpo(response: Response, porDefecto: string) {
  const body = await response.json().catch(() => null);
  if (!response.ok) throw new ApiError(
    body?.error || (typeof body?.detail === "string" ? body.detail : porDefecto),
    body?.codigo || "error_servidor",
  );
  return body;
}

/** Una foto guardada en la base desde la pestaña Registrar (#342). */
export interface RostroSubido {
  id: number;
  persona_id: number;
  persona: string | null;
  foto: string;
}

export interface Registro extends RostroSubido {
  persona_nueva: boolean;
  fotos: number;
}

export const MAX_NOMBRE = 50;

/** El mismo criterio del servidor, para avisar antes de subir nada. */
export function validarNombre(nombre: string): string | null {
  const limpio = nombre.trim().replace(/\s+/g, " ");
  if (!limpio) return "Escribe el nombre de la persona.";
  if (limpio.length > MAX_NOMBRE) return `El nombre puede tener hasta ${MAX_NOMBRE} caracteres.`;
  if (!/^[\p{L}][\p{L}'. _-]*$/u.test(limpio)) return "Usa solo letras, espacios, apóstrofos, guiones y puntos.";
  return null;
}

export async function registrar(
  imagen: Blob, nombre: string, consentimiento: boolean, signal: AbortSignal,
  base = API, request: typeof fetch = fetch,
): Promise<Registro> {
  const error = validarImagen(imagen) ?? validarNombre(nombre);
  if (error) throw new ApiError(error, "datos_invalidos");
  const form = new FormData();
  form.append("imagen", imagen, "registro");
  const params = new URLSearchParams({ nombre: nombre.trim(), consentimiento: String(consentimiento) });
  const response = await request(`${base}/rostros?${params}`, { method: "POST", body: form, signal });
  return await cuerpo(response, "El servidor no pudo registrar la foto.") as Registro;
}

export async function listarSubidos(base = API, request: typeof fetch = fetch): Promise<RostroSubido[]> {
  const response = await request(`${base}/rostros/subidos`);
  const body = await cuerpo(response, "No se pudo leer la lista de fotos registradas.");
  if (!Array.isArray(body)) throw new ApiError("El servidor devolvió una lista inválida.", "error_servidor");
  return body as RostroSubido[];
}

export async function eliminarRostro(id: number, base = API, request: typeof fetch = fetch): Promise<void> {
  const response = await request(`${base}/rostros/${Math.trunc(id)}`, { method: "DELETE" });
  await cuerpo(response, "No se pudo eliminar la foto.");
}

export function fotoUrl(ruta: string, base = API): string {
  // Solo las rutas opacas del API: nunca URLs de terceros recibidas en un resultado.
  return /^\/(personas|rostros)\/\d+\/foto$/.test(ruta) ? `${base}${ruta}` : "";
}

export function mensajeError(error: unknown): string {
  return error instanceof ApiError ? error.message : "No se pudo conectar con Rikuq. Revisa el servidor e intenta otra vez.";
}
