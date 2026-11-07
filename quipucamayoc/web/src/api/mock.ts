/**
 * Datos falsos con la forma exacta del contrato, para trabajar la interfaz sin
 * servidor (VITE_USE_MOCK=true). Las esperas imitan las medidas reales: unos
 * 2 s de reescritura, decenas de ms en QuipuDB y varios segundos de Gemini.
 */

import type {
  Agregado,
  AskRequest,
  Evento,
  Ficha,
  FiltrosPapers,
  Fuente,
  Health,
  NuevoPaper,
  PapersResponse,
  PasoPlan,
  Sql,
} from "@/api/types";

const esperar = (ms: number, senal?: AbortSignal) =>
  new Promise<void>((resolver, rechazar) => {
    const reloj = setTimeout(resolver, ms);
    senal?.addEventListener("abort", () => {
      clearTimeout(reloj);
      rechazar(new DOMException("cancelada", "AbortError"));
    });
  });

const FICHAS: Ficha[] = [
  {
    id: 1,
    arxiv_id: "1603.09320v4",
    titulo:
      "Efficient and robust approximate nearest neighbor search using Hierarchical Navigable Small World graphs",
    autores: "Yu. A. Malkov, D. A. Yashunin",
    anio: 2016,
    categoria: "cs.DS",
    abstract: "We present a new approach for the approximate K-nearest neighbor search based on navigable small world graphs with controllable hierarchy.",
    url: "https://arxiv.org/abs/1603.09320v4",
    chunks: 36,
  },
  {
    id: 2,
    arxiv_id: "1807.05614v2",
    titulo: "ANN-Benchmarks: A Benchmarking Tool for Approximate Nearest Neighbor Algorithms",
    autores: "Martin Aumüller, Erik Bernhardsson, Alexander Faithfull",
    anio: 2018,
    categoria: "cs.IR",
    abstract: "This paper describes ANN-Benchmarks, a tool for evaluating the performance of in-memory approximate nearest neighbor algorithms.",
    url: "https://arxiv.org/abs/1807.05614v2",
    chunks: 41,
  },
  {
    id: 3,
    arxiv_id: "2401.08281v4",
    titulo: "The Faiss library",
    autores: "Matthijs Douze, Alexandr Guzhva, Chengqi Deng, Jeff Johnson",
    anio: 2024,
    categoria: "cs.LG",
    abstract: "Vector databases typically manage large collections of embedding vectors.",
    url: "https://arxiv.org/abs/2401.08281v4",
    chunks: 58,
  },
];

function paso(op: string, structure: string, detail: string, hijos: PasoPlan[] = [], ms = 0.02): PasoPlan {
  return {
    op,
    structure,
    table: op === "project" || op === "sort" || op === "limit" ? null : "chunks",
    column: op === "text_search" ? "texto" : null,
    detail,
    stats: { pages_read: op === "fetch" ? 8 : op === "text_search" ? 3 : 0, pages_written: 0, records_examined: 8, records_returned: 8 },
    time_ms: ms,
    ranking: op === "text_search" ? "bm25" : null,
    metric: null,
    children: hijos,
  };
}

function sqlMatch(consulta: string): Sql {
  const sql = `SELECT id, paper_id, orden, pagina, texto, SCORE() AS relevancia FROM chunks WHERE MATCH(texto, '${consulta.replaceAll("'", "''")}') USING BM25 LIMIT 24`;
  return {
    proposito: "chunks relevantes (indice invertido)",
    sql,
    filas: 8,
    tiempo_ms: 41.3,
    plan: {
      query: sql,
      time_ms: 38.9,
      root: paso("limit", "memory", "24", [
        paso("project", "memory", "id, paper_id, orden, pagina, texto, relevancia", [
          paso("sort", "memory", "orden puesto por el indice (DESC, costo cero)", [
            paso("fetch", "heap", "lee 24 registros por RID", [
              paso("text_search", "inverted_index", `indice chunks_por_texto; MATCH(texto, '${consulta}'); ranking bm25 (elegido con USING); top-24; idioma english`, [], 31.7),
            ], 4.2),
          ]),
        ]),
      ]),
    },
  };
}

function sqlPaper(id: number): Sql {
  const sql = `SELECT id, arxiv_id, titulo, autores, anio, url FROM papers WHERE id = ${id}`;
  return {
    proposito: `paper ${id} (hash sobre id)`,
    sql,
    filas: 1,
    tiempo_ms: 6.1,
    plan: {
      query: sql,
      time_ms: 1.2,
      root: paso("project", "memory", "id, arxiv_id, titulo, autores, anio, url", [
        { ...paso("fetch", "heap", "lee 1 registros por RID", [
          { ...paso("index_search", "extendible_hash", `id = ${id}`), table: "papers", column: "id" },
        ]), table: "papers" },
      ]),
    },
  };
}

const TEXTOS = [
  "HNSW builds a hierarchy of proximity graphs. The search starts at the top layer and greedily descends, so the number of distance computations grows logarithmically with the dataset size.",
  "In our benchmarks graph-based methods such as HNSW give the best throughput at high recall on most datasets, while IVF-based indexes need many more probes to reach the same recall.",
  "IVF partitions the space with k-means and only scans the nprobe closest lists. It uses less memory than graph indexes and compresses well with product quantization.",
];

function fuentes(): Fuente[] {
  return TEXTOS.map((texto, i) => {
    const ficha = FICHAS[i]!;
    return {
      numero: i + 1,
      chunk_id: ficha.id * 10000 + i,
      orden: i,
      pagina: i + 2,
      texto,
      relevancia: 16.7 - i * 1.9,
      origen: i === 2 ? ["vectores"] : ["texto", "vectores"],
      citada: false,
      paper: { id: ficha.id, arxiv_id: ficha.arxiv_id, titulo: ficha.titulo, autores: ficha.autores, anio: ficha.anio, url: ficha.url },
    };
  });
}

export async function salud(): Promise<Health> {
  await esperar(150);
  return {
    status: "ok",
    engine: { url: "(datos falsos)", reachable: true, tables: 3, vector_search: true, error: null },
    llm: { provider: "gemini", model: "gemini-3.5-flash (falso)", configured: true },
  };
}

export async function preguntar(
  peticion: AskRequest,
  alLlegar: (evento: Evento) => void,
  senal?: AbortSignal,
): Promise<void> {
  const reescribe = peticion.reescribir && peticion.modo !== "vectores";
  const consulta = reescribe ? "HNSW IVF advantages recall latency" : peticion.pregunta;
  if (reescribe || peticion.modo !== "texto") await esperar(900, senal);
  alLlegar({ etapa: "consulta", consulta });

  await esperar(300, senal);
  const encontradas = fuentes().slice(0, peticion.k);
  const sql = [sqlMatch(consulta), ...encontradas.map((f) => sqlPaper(f.paper.id))];
  alLlegar({ etapa: "recuperacion", fuentes: encontradas, sql, recuperacion_ms: 68 });

  await esperar(1800, senal);
  const citas = [1, 2, 3].filter((n) => n <= encontradas.length);
  alLlegar({
    etapa: "respuesta",
    respuesta: {
      pregunta: peticion.pregunta,
      consulta,
      respuesta:
        "Según los fragmentos, **HNSW** tiene dos ventajas principales sobre **IVF**:\n\n" +
        "* Alcanza más *recall* con menos latencia: la búsqueda baja por capas de un grafo y hace pocas comparaciones [1][2].\n" +
        "* En los benchmarks es el más rápido con recall alto, mientras IVF necesita revisar más listas (`nprobe`) [2].\n\n" +
        "A cambio, IVF usa menos memoria y se combina bien con cuantización de productos [3].",
      modelo: "gemini-3.5-flash (falso)",
      ranking: peticion.ranking,
      modo: peticion.modo,
      citas,
      citas_descartadas: [],
      fuentes: encontradas.map((f) => ({ ...f, citada: citas.includes(f.numero) })),
      sql,
      tiempos: {
        embedding_ms: peticion.modo === "texto" ? 0 : 420,
        reescritura_ms: reescribe ? 1905 : 0,
        recuperacion_ms: 68,
        generacion_ms: 6620,
        total_ms: 8610,
      },
    },
  });
}

export async function papers(filtros: FiltrosPapers): Promise<PapersResponse> {
  await esperar(200);
  const desde = filtros.anio_desde ?? 0;
  const hasta = filtros.anio_hasta ?? 9999;
  const autor = (filtros.autor ?? "").toLowerCase();
  const rango = filtros.anio_desde !== undefined || filtros.anio_hasta !== undefined;
  const sql = rango
    ? `SELECT id, arxiv_id, titulo, autores, anio, categoria, abstract, url FROM papers WHERE anio BETWEEN ${desde} AND ${hasta}`
    : "SELECT id, arxiv_id, titulo, autores, anio, categoria, abstract, url FROM papers";
  return {
    papers: FICHAS.filter((f) => f.anio >= desde && f.anio <= hasta && f.autores.toLowerCase().includes(autor)),
    sql: [
      { proposito: rango ? "papers por rango de anios (B+ sobre anio)" : "todos los papers", sql, plan: null, filas: FICHAS.length, tiempo_ms: 12.4 },
      { proposito: "chunks por paper (GROUP BY)", sql: "SELECT paper_id, COUNT(*) FROM chunks GROUP BY paper_id", plan: null, filas: FICHAS.length, tiempo_ms: 210.7 },
    ],
  };
}

export async function agregar(nuevo: NuevoPaper): Promise<Agregado> {
  await esperar(1500);
  FICHAS.push({
    id: FICHAS.length + 1,
    arxiv_id: `local-${Date.now().toString(16)}`,
    titulo: nuevo.titulo,
    autores: nuevo.autores,
    anio: nuevo.anio,
    categoria: "local",
    abstract: nuevo.abstract,
    url: "",
    chunks: 12,
  });
  return {
    arxiv_id: FICHAS.at(-1)!.arxiv_id,
    chunks: 12,
    vectores: 12,
    aviso: null,
    tiempos: { extraccion_s: 1.1, chunking_s: 0.02, carga_s: 0.05, indice_s: 3.0 },
  };
}
