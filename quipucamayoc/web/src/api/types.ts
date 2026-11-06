/**
 * Espejo de quipucamayoc/server/app/contrato.py y de /health en main.py.
 * Cambiar un campo alla es cambiarlo aqui.
 */

export interface Health {
  status: "ok" | "degraded";
  engine: {
    url: string;
    reachable: boolean;
    tables: number | null;
    error: string | null;
  };
  llm: {
    provider: string;
    model: string;
    configured: boolean;
  };
}

export type Ranking = "BM25" | "TF_IDF";

export interface AskRequest {
  pregunta: string;
  k: number;
  ranking: Ranking;
  reescribir: boolean;
}

export interface Paper {
  id: number;
  arxiv_id: string;
  titulo: string;
  autores: string;
  anio: number;
  url: string;
}

export interface Fuente {
  numero: number;
  chunk_id: number;
  orden: number;
  pagina: number;
  texto: string;
  relevancia: number;
  citada: boolean;
  paper: Paper;
}

/** Un paso del plan del motor (ADR 0002). `stats` y `time_ms` son propios. */
export interface PasoPlan {
  op: string;
  structure: string;
  table: string | null;
  column: string | null;
  detail: string;
  stats: {
    pages_read: number;
    pages_written: number;
    records_examined: number;
    records_returned: number;
  };
  time_ms: number;
  ranking: string | null;
  metric: string | null;
  children: PasoPlan[];
}

export interface Plan {
  query: string;
  time_ms: number;
  root: PasoPlan;
}

export interface Sql {
  proposito: string;
  sql: string;
  plan: Plan | null;
  filas: number;
  tiempo_ms: number;
}

export interface Tiempos {
  reescritura_ms: number;
  recuperacion_ms: number;
  generacion_ms: number;
  total_ms: number;
}

export interface AskResponse {
  pregunta: string;
  consulta: string;
  respuesta: string;
  modelo: string | null;
  ranking: Ranking;
  citas: number[];
  citas_descartadas: number[];
  fuentes: Fuente[];
  sql: Sql[];
  tiempos: Tiempos;
}

export type Origen = "motor" | "llm";

/** Las lineas de POST /ask/stream, en este orden; `error` puede llegar en cualquiera. */
export type Evento =
  | { etapa: "consulta"; consulta: string }
  | { etapa: "recuperacion"; fuentes: Fuente[]; sql: Sql[]; recuperacion_ms: number }
  | { etapa: "respuesta"; respuesta: AskResponse }
  | { etapa: "error"; error: string; origen: Origen };

export interface Ficha {
  id: number;
  arxiv_id: string;
  titulo: string;
  autores: string;
  anio: number;
  categoria: string;
  abstract: string;
  url: string;
  chunks: number;
}

export interface PapersResponse {
  papers: Ficha[];
  sql: Sql[];
}

export interface FiltrosPapers {
  anio_desde?: number;
  anio_hasta?: number;
  autor?: string;
}

export interface NuevoPaper {
  archivo: File;
  titulo: string;
  autores: string;
  anio: number;
  abstract: string;
}

export interface Agregado {
  arxiv_id: string;
  chunks: number;
  tiempos: {
    extraccion_s: number;
    chunking_s: number;
    carga_s: number;
    indice_s: number;
  };
}
