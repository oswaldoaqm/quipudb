/**
 * Unico punto por el que la interfaz habla con el motor.
 *
 * Los paneles llaman siempre a estas funciones, sin saber si detras hay una
 * API o los datos falsos de `mock.ts`. Cuando `engine/api/` exista, basta con
 * poner VITE_USE_MOCK=false: ningun componente cambia.
 */

import { MotorError } from "@/api/errors";
import { mockExecuteQuery, mockListTables, mockLoadCsv } from "@/api/mock";
import type {
  LoadResult,
  MediaJob,
  QueryError,
  QueryResult,
  TableInfo,
  UploadResult,
  VocabularyInfo,
  VocabularyJob,
} from "@/api/types";

const URL_API = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

/** Sin motor levantado se usan datos falsos; es el modo por defecto. */
export const USA_DATOS_FALSOS = import.meta.env.VITE_USE_MOCK !== "false";

export { MotorError };

async function pedir<T>(ruta: string, init?: RequestInit): Promise<T> {
  let respuesta: Response;
  try {
    respuesta = await fetch(`${URL_API}${ruta}`, {
      // Un cuerpo de FormData va sin cabecera propia a proposito: el navegador
      // tiene que escribir el `multipart/form-data; boundary=...` el mismo, y
      // ponerlo a mano deja la peticion sin boundary y el servidor la rechaza.
      headers:
        init?.body instanceof FormData
          ? undefined
          : { "Content-Type": "application/json" },
      ...init,
    });
  } catch {
    throw MotorError.sinUbicacion(
      `No se pudo contactar al motor en ${URL_API}. Levantalo, o usa VITE_USE_MOCK=true`,
    );
  }

  if (!respuesta.ok) {
    const detalle = (await respuesta.json().catch(() => null)) as QueryError | null;
    throw detalle?.error
      ? new MotorError(detalle)
      : MotorError.sinUbicacion(`El motor respondio ${respuesta.status}`);
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

/**
 * Carga un CSV con cabecera en una tabla que ya existe.
 *
 * El archivo viaja tal cual: quien lo parsea es la API, que lo lee en
 * streaming y no lo carga entero en memoria (issue #114). El frontend solo
 * informa de lo que responde.
 *
 * Va sin `atomic`, asi que se queda con el valor por defecto de la API: las
 * filas buenas entran aunque alguna falle. Es lo que pide el criterio 3, que
 * quiere el conteo de las que entraron Y el de las que no.
 */
export function loadCsv(tabla: string, archivo: File): Promise<LoadResult> {
  if (USA_DATOS_FALSOS) {
    return mockLoadCsv(tabla, archivo);
  }

  const cuerpo = new FormData();
  cuerpo.append("file", archivo);
  return pedir<LoadResult>(`/tables/${encodeURIComponent(tabla)}/load`, {
    method: "POST",
    body: cuerpo,
  });
}

/** Lista las tablas del catalogo con su organizacion, columnas e indices. */
export function listTables(): Promise<TableInfo[]> {
  if (USA_DATOS_FALSOS) {
    return mockListTables();
  }
  return pedir<TableInfo[]>("/tables");
}

/*
 * Carga multimedia (#289). Los datos falsos no tienen archivos ni vectores
 * que extraer, asi que estas funciones solo existen con el motor real.
 */

function soloConMotor(): never {
  throw MotorError.sinUbicacion(
    "La carga de carpetas necesita el motor real: pon VITE_USE_MOCK=false",
  );
}

/** Sube archivos a una carpeta nueva de `subidas/`, o a `folder` si viene. */
export function uploadMedia(
  archivos: { name: string; file: Blob }[],
  destino: { folder?: string; name?: string },
): Promise<UploadResult> {
  if (USA_DATOS_FALSOS) soloConMotor();
  const cuerpo = new FormData();
  for (const { name, file } of archivos) cuerpo.append("files", file, name);
  if (destino.folder) cuerpo.append("folder", destino.folder);
  if (destino.name) cuerpo.append("name", destino.name);
  return pedir<UploadResult>("/media/uploads", { method: "POST", body: cuerpo });
}

export function listVocabularies(): Promise<VocabularyInfo[]> {
  if (USA_DATOS_FALSOS) return Promise.resolve([]);
  return pedir<VocabularyInfo[]>("/vocabularies");
}

export function trainVocabulary(peticion: {
  folder: string;
  k: number;
  table?: string;
  name?: string;
  recursive?: boolean;
}): Promise<VocabularyJob> {
  if (USA_DATOS_FALSOS) soloConMotor();
  return pedir<VocabularyJob>("/vocabularies", {
    method: "POST",
    body: JSON.stringify(peticion),
  });
}

export function vocabularyJob(id: string): Promise<VocabularyJob> {
  return pedir<VocabularyJob>(`/vocabularies/${encodeURIComponent(id)}`);
}

export function registerVocabulary(tabla: string, vocabulary: string): Promise<VocabularyInfo> {
  if (USA_DATOS_FALSOS) soloConMotor();
  return pedir<VocabularyInfo>(`/tables/${encodeURIComponent(tabla)}/vocabulary`, {
    method: "PUT",
    body: JSON.stringify({ vocabulary }),
  });
}

export function loadMedia(
  tabla: string,
  peticion: { folder: string; vocabulary: string; recursive?: boolean; start_id?: number },
): Promise<MediaJob> {
  if (USA_DATOS_FALSOS) soloConMotor();
  return pedir<MediaJob>(`/tables/${encodeURIComponent(tabla)}/load-media`, {
    method: "POST",
    body: JSON.stringify(peticion),
  });
}

export function mediaJob(tabla: string, id: string): Promise<MediaJob> {
  return pedir<MediaJob>(
    `/tables/${encodeURIComponent(tabla)}/load-media/${encodeURIComponent(id)}`,
  );
}
