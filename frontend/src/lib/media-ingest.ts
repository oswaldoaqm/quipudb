/**
 * De una carpeta del computador a una tabla consultable con SIMILAR_TO (#289).
 *
 * Son tres pasos contra la API: subir los archivos (#288), entrenar o elegir
 * el vocabulario (#287) y cargar la carpeta (#187). Este modulo los encadena
 * sin saber de React ni de `fetch`: recibe las funciones de la API, asi se
 * prueba con una API simulada y el panel solo dibuja el avance.
 */

import type {
  MediaFileError,
  MediaJob,
  TableInfo,
  UploadResult,
  VocabularyJob,
} from "@/api/types";

/** Las extensiones que lee el pipeline; copia de `engine/multimedia/features.py`. */
export const EXTENSIONES_IMAGEN = [".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"];
export const EXTENSIONES_AUDIO = [".wav", ".flac", ".ogg", ".mp3", ".aiff", ".aif"];

/** Por peticion; la API acepta hasta 500, pero tandas chicas muestran avance. */
export const ARCHIVOS_POR_TANDA = 100;

export interface ArchivoElegido {
  /** Ruta dentro de la carpeta elegida, como `vacaciones/playa.png`. */
  name: string;
  file: Blob;
}

export interface MediaApi {
  upload(
    archivos: ArchivoElegido[],
    destino: { folder?: string; name?: string },
  ): Promise<UploadResult>;
  train(peticion: {
    folder: string;
    k: number;
    table: string;
    name: string;
    recursive: boolean;
  }): Promise<VocabularyJob>;
  trainStatus(id: string): Promise<VocabularyJob>;
  register(tabla: string, vocabulary: string): Promise<unknown>;
  /** El primer id libre: el mayor id de la tabla mas uno, o 1 si esta vacia. */
  nextId(tabla: string): Promise<number>;
  load(
    tabla: string,
    peticion: { folder: string; vocabulary: string; recursive: boolean; start_id: number },
  ): Promise<MediaJob>;
  loadStatus(tabla: string, id: string): Promise<MediaJob>;
}

export type Avance =
  | { fase: "subiendo"; hechos: number; total: number }
  | { fase: "entrenando"; hechos: number; total: number }
  | { fase: "cargando"; hechos: number; total: number };

export type Vocabulario =
  | { tipo: "nuevo"; k: number }
  | { tipo: "existente"; path: string };

export interface InformeCarga {
  folder: string;
  subidos: number;
  vocabulary: string;
  /** Huella y cantidad de objetos, solo si se entreno en esta carga. */
  entrenado: { documents: number | null; codebook_id: string | null } | null;
  inserted: number;
  failed: number;
  /** Rechazos de la subida, del entrenamiento y de la carga, en ese orden. */
  errors: MediaFileError[];
  errors_truncated: boolean;
  /** Ruta de un archivo cargado, para armar una consulta de ejemplo. */
  ejemplo: string | null;
}

/** Las columnas que exige `validate_media_schema` del motor, con sus nombres por omision. */
export const COLUMNAS_MULTIMEDIA = ["id", "nombre", "ruta", "rasgos"];

/**
 * Si se puede cargar una carpeta en la tabla y con que K. Se revisa antes de
 * subir nada: entrenar diez minutos para que la carga falle por el esquema
 * seria trabajo tirado.
 */
export function revisarTabla(tabla: TableInfo): { dimension: number } | { motivo: string } {
  const porNombre = new Map(tabla.columns.map((c) => [c.name, c]));
  const exactas =
    tabla.columns.length === 4 && COLUMNAS_MULTIMEDIA.every((n) => porNombre.has(n));
  const id = porNombre.get("id");
  const rasgos = porNombre.get("rasgos");
  if (
    !exactas ||
    id?.type !== "INT" ||
    !id.is_primary_key ||
    porNombre.get("nombre")?.type !== "VARCHAR" ||
    porNombre.get("ruta")?.type !== "VARCHAR" ||
    rasgos?.type !== "VECTOR" ||
    rasgos.size === null
  ) {
    return {
      motivo:
        "Para cargar una carpeta, la tabla necesita exactamente id INT PRIMARY KEY, " +
        "nombre VARCHAR, ruta VARCHAR y rasgos VECTOR(K).",
    };
  }
  return { dimension: rasgos.size };
}

function extension(nombre: string): string {
  const punto = nombre.lastIndexOf(".");
  return punto < 0 ? "" : nombre.slice(punto).toLowerCase();
}

export function esMultimedia(nombre: string): boolean {
  const ext = extension(nombre);
  return EXTENSIONES_IMAGEN.includes(ext) || EXTENSIONES_AUDIO.includes(ext);
}

/**
 * Separa lo que vale la pena subir. Una carpeta real trae `Thumbs.db`,
 * `.DS_Store` o un `leeme.txt`: el servidor los rechazaria igual, pero
 * mandarlos es gastar subida.
 */
export function filtrarArchivos(archivos: ArchivoElegido[]): {
  multimedia: ArchivoElegido[];
  omitidos: number;
  imagenes: number;
  audios: number;
} {
  const multimedia = archivos.filter((a) => esMultimedia(a.name));
  const imagenes = multimedia.filter((a) => EXTENSIONES_IMAGEN.includes(extension(a.name))).length;
  return {
    multimedia,
    omitidos: archivos.length - multimedia.length,
    imagenes,
    audios: multimedia.length - imagenes,
  };
}

/** Lo que acepta `name` en `POST /vocabularies`: letras, digitos, `-` y `_`. */
export function nombreDeVocabulario(carpeta: string, k: number): string {
  const base = carpeta.normalize("NFD").replace(/[\u0300-\u036f]/g, "")
    .replace(/[^A-Za-z0-9_-]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 48);
  return `${base || "vocabulario"}-k${k}`;
}

const TERMINALES = new Set(["completed", "failed", "cancelled"]);

async function esperar<T extends { status: string; error: string | null }>(
  consultar: () => Promise<T>,
  alAvanzar: (trabajo: T) => void,
  pausa: (ms: number) => Promise<void>,
  intervaloMs: number,
): Promise<T> {
  for (;;) {
    const trabajo = await consultar();
    alAvanzar(trabajo);
    if (TERMINALES.has(trabajo.status)) {
      if (trabajo.status !== "completed") {
        throw new Error(trabajo.error ?? `el trabajo termino como ${trabajo.status}`);
      }
      return trabajo;
    }
    await pausa(intervaloMs);
  }
}

const dormir = (ms: number) => new Promise<void>((listo) => setTimeout(listo, ms));

/**
 * Sube, entrena o asocia el vocabulario y carga. Lanza `Error` con el motivo
 * si un paso falla entero; los archivos que fallan solos van al informe.
 */
export async function cargarCarpeta(
  api: MediaApi,
  opciones: {
    tabla: string;
    nombre: string;
    archivos: ArchivoElegido[];
    vocabulario: Vocabulario;
  },
  alAvanzar: (avance: Avance) => void,
  { tanda = ARCHIVOS_POR_TANDA, intervaloMs = 400, pausa = dormir } = {},
): Promise<InformeCarga> {
  const { tabla, nombre, archivos, vocabulario } = opciones;
  if (archivos.length === 0) throw new Error("la carpeta no tiene imagenes ni audios");

  const errors: MediaFileError[] = [];
  let truncados = false;
  let folder: string | undefined;
  let subidos = 0;
  let ejemplo: string | null = null;
  alAvanzar({ fase: "subiendo", hechos: 0, total: archivos.length });
  for (let inicio = 0; inicio < archivos.length; inicio += tanda) {
    const parte = archivos.slice(inicio, inicio + tanda);
    // La primera tanda crea la carpeta; las demas se agregan a esa misma.
    const respuesta = await api.upload(parte, folder ? { folder } : { name: nombre });
    folder = respuesta.folder;
    subidos += respuesta.saved;
    ejemplo ??= respuesta.files[0]?.path ?? null;
    errors.push(...respuesta.rejected.map((r) => ({ path: r.name, error: r.error })));
    alAvanzar({ fase: "subiendo", hechos: inicio + parte.length, total: archivos.length });
  }
  if (folder === undefined || subidos === 0) {
    throw new Error(
      `no se subio ningun archivo${errors[0] ? `: ${errors[0].path}: ${errors[0].error}` : ""}`,
    );
  }
  // Las subcarpetas del navegador quedan dentro de la carpeta subida.
  const recursive = archivos.some((a) => a.name.includes("/"));

  let vocabularyPath: string;
  let entrenado: InformeCarga["entrenado"] = null;
  if (vocabulario.tipo === "nuevo") {
    const inicial = await api.train({
      folder,
      k: vocabulario.k,
      table: tabla,
      // Sin nombre, la API usaria el de la carpeta subida, que lleva un id
      // al final; con el de la carpeta elegida se reconoce en la lista.
      name: nombreDeVocabulario(nombre, vocabulario.k),
      recursive,
    });
    const listo = await esperar(
      () => api.trainStatus(inicial.job_id),
      (t) => alAvanzar({ fase: "entrenando", hechos: t.described + t.failed, total: t.selected || subidos }),
      pausa,
      intervaloMs,
    );
    if (listo.vocabulary === null) throw new Error("el entrenamiento no devolvio un vocabulario");
    vocabularyPath = listo.vocabulary;
    entrenado = { documents: listo.documents, codebook_id: listo.codebook_id };
    errors.push(...listo.errors);
    truncados ||= listo.errors_truncated;
  } else {
    vocabularyPath = vocabulario.path;
    // Sin esto la carga funciona, pero SIMILAR_TO no sabria con que
    // vocabulario describir la consulta.
    await api.register(tabla, vocabularyPath);
  }

  // load-media numera desde start_id; con 1 fijo, una segunda carpeta en la
  // misma tabla chocaria con las claves de la primera.
  const start_id = await api.nextId(tabla);
  const carga = await api.load(tabla, { folder, vocabulary: vocabularyPath, recursive, start_id });
  const final = await esperar(
    () => api.loadStatus(tabla, carga.job_id),
    (t) => alAvanzar({ fase: "cargando", hechos: t.processed, total: Math.max(t.discovered, subidos) }),
    pausa,
    intervaloMs,
  );
  errors.push(...final.errors);
  truncados ||= final.errors_truncated;

  return {
    folder,
    subidos,
    vocabulary: vocabularyPath,
    entrenado,
    inserted: final.inserted,
    failed: final.failed,
    errors,
    errors_truncated: truncados,
    ejemplo,
  };
}

/**
 * Una consulta SIMILAR_TO lista para el editor. El motor exige `USING`; se
 * usa el indice vectorial que tenga la tabla y, si no tiene, HNSW, que en
 * ese caso cae a la busqueda exacta (`docs/consultas-multimedia.md`).
 */
export function consultaDeEjemplo(tabla: string, ruta: string, estructuras: string[]): string {
  const indice = estructuras.find((e) => e === "hnsw" || e === "ivf") ?? "hnsw";
  const literal = ruta.replaceAll("'", "''");
  return (
    `SELECT id, nombre, SIMILARITY_SCORE() AS score FROM ${tabla}\n` +
    `WHERE SIMILAR_TO('${literal}', k=5)\n` +
    `USING ${indice.toUpperCase()} WITH METRIC=cosine\n` +
    "ORDER BY score DESC"
  );
}

/**
 * Sube un archivo suelto para usarlo en SIMILAR_TO (#290) y devuelve su ruta
 * en el servidor. Cada archivo va a su propia carpeta de `subidas/`, asi dos
 * consultas con `foto.jpg` no chocan.
 */
export async function subirArchivoDeConsulta(
  upload: MediaApi["upload"],
  archivo: ArchivoElegido,
): Promise<string> {
  if (!esMultimedia(archivo.name)) {
    throw new Error(`${archivo.name} no es una imagen ni un audio que el motor pueda describir`);
  }
  const respuesta = await upload([archivo], { name: "consulta" });
  const guardado = respuesta.files[0];
  if (!guardado) {
    const motivo = respuesta.rejected[0]?.error ?? "el servidor no lo guardo";
    throw new Error(`no se pudo subir ${archivo.name}: ${motivo}`);
  }
  return guardado.path;
}

/**
 * Pone la ruta en el texto donde esta la seleccion, como literal SQL.
 *
 * Si la seleccion ya esta entre comillas simples, como al escribir
 * `SIMILAR_TO('|', k=5)`, entra sin comillas propias; si no, entra con ellas.
 * Una comilla en la ruta se duplica, igual que en cualquier literal. Devuelve
 * el texto nuevo y donde queda el cursor, en offsets de caracteres.
 */
export function insertarRuta(
  texto: string,
  inicio: number,
  fin: number,
  ruta: string,
): { texto: string; cursor: number } {
  const escapada = ruta.replaceAll("'", "''");
  const entreComillas = texto[inicio - 1] === "'" && texto[fin] === "'";
  const pieza = entreComillas ? escapada : `'${escapada}'`;
  return {
    texto: texto.slice(0, inicio) + pieza + texto.slice(fin),
    cursor: inicio + pieza.length,
  };
}
