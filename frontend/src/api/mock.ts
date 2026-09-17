/**
 * Datos falsos para trabajar sin motor (criterio 4 del issue #33).
 *
 * Los planes son los tres ejemplos de ADR 0002, con sus contadores intactos:
 * sirven de referencia viva de la forma que el panel debe saber dibujar.
 */

import type { Plan, QueryResult, TableInfo } from "@/api/types";

export const MOCK_TABLES: TableInfo[] = [
  {
    name: "alumnos",
    storage: "heap",
    record_count: 10000,
    columns: [
      { name: "codigo", type: "INT", size: null, is_primary_key: true },
      { name: "nombre", type: "VARCHAR", size: 32, is_primary_key: false },
      { name: "promedio", type: "DOUBLE", size: null, is_primary_key: false },
    ],
    indexes: [
      {
        name: "por_promedio",
        column: "promedio",
        structure: "bplus_unclustered",
        supports_range: true,
      },
    ],
  },
  {
    name: "cursos",
    storage: "bplus_clustered",
    record_count: 120,
    columns: [
      { name: "id", type: "INT", size: null, is_primary_key: true },
      { name: "nombre", type: "VARCHAR", size: 40, is_primary_key: false },
      { name: "creditos", type: "INT", size: null, is_primary_key: false },
    ],
    indexes: [],
  },
  {
    name: "matriculas",
    storage: "sequential",
    record_count: 4300,
    columns: [
      { name: "id", type: "INT", size: null, is_primary_key: true },
      { name: "codigo_alumno", type: "INT", size: null, is_primary_key: false },
      { name: "ciclo", type: "VARCHAR", size: 8, is_primary_key: false },
      { name: "activa", type: "BOOL", size: null, is_primary_key: false },
    ],
    indexes: [
      {
        name: "por_alumno",
        column: "codigo_alumno",
        structure: "extendible_hash",
        supports_range: false,
      },
    ],
  },
];

/** La consulta que el panel ejecuta al abrir, para probar el camino completo. */
export const CONSULTA_DE_PRUEBA =
  "SELECT * FROM alumnos WHERE promedio BETWEEN 15 AND 17";

const NOMBRES = [
  "ana torres",
  "bruno diaz",
  "carla ruiz",
  "diego salas",
  "elena vega",
  "fabio ramos",
  "gina soto",
  "hugo lima",
  "irene paz",
  "julio cano",
  "karla mora",
  "luis bravo",
];

const FILAS_POR_RANGO = NOMBRES.map((nombre, indice) => [
  1000 + indice,
  nombre,
  Number((15 + (indice % 5) * 0.4).toFixed(1)),
]);

/** ADR 0002: busqueda por rango con indice secundario sobre un heap. */
const PLAN_RANGO: Plan = {
  query: CONSULTA_DE_PRUEBA,
  time_ms: 2.1,
  totals: {
    pages_read: 13,
    pages_written: 0,
    records_examined: 24,
    records_returned: 24,
  },
  root: {
    op: "fetch",
    structure: "heap",
    table: "alumnos",
    column: null,
    detail: "lee 12 registros por RID",
    stats: {
      pages_read: 9,
      pages_written: 0,
      records_examined: 12,
      records_returned: 12,
    },
    time_ms: 1.2,
    children: [
      {
        op: "index_range",
        structure: "bplus_unclustered",
        table: "alumnos",
        column: "promedio",
        detail: "promedio en [15, 17]",
        stats: {
          pages_read: 4,
          pages_written: 0,
          records_examined: 12,
          records_returned: 12,
        },
        time_ms: 0.6,
        children: [],
      },
    ],
  },
};

/** ADR 0002: ORDER BY sin indice, external sorting sobre un scan. */
const PLAN_ORDEN: Plan = {
  query: "SELECT * FROM alumnos ORDER BY nombre",
  time_ms: 48,
  totals: {
    pages_read: 120,
    pages_written: 80,
    records_examined: 20000,
    records_returned: 20000,
  },
  root: {
    op: "sort",
    structure: "external_sort",
    table: "alumnos",
    column: "nombre",
    detail: "k-way merge, 4 runs",
    stats: {
      pages_read: 80,
      pages_written: 80,
      records_examined: 10000,
      records_returned: 10000,
    },
    time_ms: 31,
    children: [
      {
        op: "scan",
        structure: "heap",
        table: "alumnos",
        column: null,
        detail: "",
        stats: {
          pages_read: 40,
          pages_written: 0,
          records_examined: 10000,
          records_returned: 10000,
        },
        time_ms: 14,
        children: [],
      },
    ],
  },
};

/** ADR 0002: busqueda puntual por clave primaria en un B+ agrupado. */
const PLAN_PUNTUAL: Plan = {
  query: "SELECT * FROM cursos WHERE id = 42",
  time_ms: 0.41,
  totals: {
    pages_read: 3,
    pages_written: 0,
    records_examined: 1,
    records_returned: 1,
  },
  root: {
    op: "search",
    structure: "bplus_clustered",
    table: "cursos",
    column: "id",
    detail: "id = 42",
    stats: {
      pages_read: 3,
      pages_written: 0,
      records_examined: 1,
      records_returned: 1,
    },
    time_ms: 0.3,
    children: [],
  },
};

const RESPUESTAS: { patron: RegExp; resultado: QueryResult }[] = [
  {
    patron: /between/i,
    resultado: {
      columns: ["codigo", "nombre", "promedio"],
      rows: FILAS_POR_RANGO,
      affected_rows: 0,
      plan: PLAN_RANGO,
    },
  },
  {
    patron: /order\s+by/i,
    resultado: {
      columns: ["codigo", "nombre", "promedio"],
      rows: [...FILAS_POR_RANGO].sort((a, b) =>
        String(a[1]).localeCompare(String(b[1])),
      ),
      affected_rows: 0,
      plan: PLAN_ORDEN,
    },
  },
  {
    patron: /from\s+cursos/i,
    resultado: {
      columns: ["id", "nombre", "creditos"],
      rows: [[42, "Base de Datos 2", 4]],
      affected_rows: 0,
      plan: PLAN_PUNTUAL,
    },
  },
];

const RESPUESTA_POR_DEFECTO: QueryResult = {
  columns: ["codigo", "nombre", "promedio"],
  rows: FILAS_POR_RANGO.slice(0, 5),
  affected_rows: 0,
  plan: PLAN_RANGO,
};

/** Responde como lo haria la API, con una latencia que deja ver el estado de carga. */
export async function mockExecuteQuery(sql: string): Promise<QueryResult> {
  await new Promise((listo) => setTimeout(listo, 150));

  const encontrada = RESPUESTAS.find(({ patron }) => patron.test(sql));
  const resultado = encontrada?.resultado ?? RESPUESTA_POR_DEFECTO;

  // El plan lleva la consulta que el usuario escribio, no la del ejemplo.
  return {
    ...resultado,
    plan: resultado.plan ? { ...resultado.plan, query: sql } : null,
  };
}

export async function mockListTables(): Promise<TableInfo[]> {
  await new Promise((listo) => setTimeout(listo, 150));
  return MOCK_TABLES;
}
