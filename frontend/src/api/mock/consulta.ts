/**
 * Interprete minimo de SELECT sobre los datos falsos.
 *
 * Cubre exactamente lo que el motor acepta hoy: una sola condicion en WHERE
 * (`=`, `<`, `<=`, `>`, `>=` o BETWEEN inclusivo), ORDER BY con ASC/DESC y
 * proyeccion de columnas. No mas, porque `Parser._condition()` tampoco encadena
 * condiciones y `OR` esta entre las palabras que rechaza.
 *
 * El plan que produce sigue las reglas del ADR 0002: la ruta de acceso se
 * elige por los indices disponibles, un indice secundario siempre son dos
 * pasos (busqueda + fetch), y un hash nunca resuelve un rango.
 *
 * Los contadores de paginas son estimaciones plausibles, no mediciones: el
 * motor real los reporta desde `OpStats`.
 */

import type { CellValue, Plan, Stats, Step, Structure } from "@/api/types";
import { type TablaFalsa } from "@/api/mock/datos";

const REGISTROS_POR_PAGINA = 128;
const MS_POR_PAGINA = 0.035;

export type Operador = "=" | "<" | "<=" | ">" | ">=";

export type Condicion =
  | { tipo: "comparacion"; columna: string; operador: Operador; valor: CellValue }
  | { tipo: "between"; columna: string; desde: CellValue; hasta: CellValue };

export interface ConsultaLeida {
  columnas: string[] | null;
  tabla: string;
  where: Condicion | null;
  orden: { columna: string; descendente: boolean } | null;
}

const SELECT_FROM = /\bSELECT\s+(.+?)\s+FROM\s+([a-zA-Z_][a-zA-Z0-9_]*)/is;
const COMPARACION =
  /\bWHERE\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*(<=|>=|=|<|>)\s*('[^']*'|-?[\d.]+|true|false)/i;
const ENTRE =
  /\bWHERE\s+([a-zA-Z_][a-zA-Z0-9_]*)\s+BETWEEN\s+('[^']*'|-?[\d.]+)\s+AND\s+('[^']*'|-?[\d.]+)/i;
const ORDEN = /\bORDER\s+BY\s+([a-zA-Z_][a-zA-Z0-9_]*)(?:\s+(ASC|DESC))?/i;

function valorDe(texto: string): CellValue {
  if (texto.startsWith("'")) return texto.slice(1, -1);
  if (/^true$/i.test(texto)) return true;
  if (/^false$/i.test(texto)) return false;
  return Number(texto);
}

/** Lee la consulta con expresiones regulares; no pretende ser un parser. */
export function leerConsulta(sql: string): ConsultaLeida | null {
  const cabeza = SELECT_FROM.exec(sql);
  if (!cabeza) return null;

  const lista = cabeza[1].trim();
  const entre = ENTRE.exec(sql);
  const comparacion = entre ? null : COMPARACION.exec(sql);
  const orden = ORDEN.exec(sql);

  return {
    columnas:
      lista === "*"
        ? null
        : lista
            .split(",")
            .map((n) => n.trim().toLowerCase())
            .filter(Boolean),
    tabla: cabeza[2].toLowerCase(),
    where: entre
      ? {
          tipo: "between",
          columna: entre[1].toLowerCase(),
          desde: valorDe(entre[2]),
          hasta: valorDe(entre[3]),
        }
      : comparacion
        ? {
            tipo: "comparacion",
            columna: comparacion[1].toLowerCase(),
            operador: comparacion[2] as Operador,
            valor: valorDe(comparacion[3]),
          }
        : null,
    orden: orden
      ? {
          columna: orden[1].toLowerCase(),
          descendente: /desc/i.test(orden[2] ?? ""),
        }
      : null,
  };
}

/** Orden total entre valores del mismo tipo, como el `compare` del core. */
function comparar(a: CellValue, b: CellValue): number {
  if (a === null || b === null) return a === b ? 0 : a === null ? -1 : 1;
  if (typeof a === "number" && typeof b === "number") return a - b;
  if (typeof a === "boolean" && typeof b === "boolean")
    return Number(a) - Number(b);
  return String(a).localeCompare(String(b));
}

function cumple(valor: CellValue, condicion: Condicion): boolean {
  if (condicion.tipo === "between") {
    return (
      comparar(valor, condicion.desde) >= 0 &&
      comparar(valor, condicion.hasta) <= 0
    );
  }
  const signo = comparar(valor, condicion.valor);
  switch (condicion.operador) {
    case "=":
      return signo === 0;
    case "<":
      return signo < 0;
    case "<=":
      return signo <= 0;
    case ">":
      return signo > 0;
    case ">=":
      return signo >= 0;
  }
}

function paginas(filas: number): number {
  return Math.max(1, Math.ceil(filas / REGISTROS_POR_PAGINA));
}

function stats(leidas: number, examinados: number, devueltos: number): Stats {
  return {
    pages_read: leidas,
    pages_written: 0,
    records_examined: examinados,
    records_returned: devueltos,
  };
}

function paso(
  op: Step["op"],
  structure: Structure,
  tabla: string,
  columna: string | null,
  detail: string,
  propias: Stats,
  hijos: Step[] = [],
): Step {
  return {
    op,
    structure,
    table: tabla,
    column: columna,
    detail,
    stats: propias,
    time_ms: Number((propias.pages_read * MS_POR_PAGINA).toFixed(2)),
    children: hijos,
  };
}

function totales(step: Step): Stats {
  return step.children.reduce(
    (suma, hijo) => {
      const t = totales(hijo);
      return {
        pages_read: suma.pages_read + t.pages_read,
        pages_written: suma.pages_written + t.pages_written,
        records_examined: suma.records_examined + t.records_examined,
        records_returned: suma.records_returned + t.records_returned,
      };
    },
    { ...step.stats },
  );
}

function describir(condicion: Condicion): string {
  return condicion.tipo === "between"
    ? `${condicion.columna} en [${condicion.desde}, ${condicion.hasta}]`
    : `${condicion.columna} ${condicion.operador} ${condicion.valor}`;
}

/**
 * Elige la ruta de acceso igual que el planner: clave primaria si la condicion
 * cae sobre ella, indice secundario aplicable si lo hay, y `scan` + `filter`
 * cuando ninguna estructura resuelve el predicado.
 */
function rutaDeAcceso(
  tabla: TablaFalsa,
  where: Condicion | null,
  coincidencias: number,
): Step {
  const total = tabla.filas.length;
  const nombre = tabla.info.name;
  const paginasTabla = paginas(total);

  if (!where) {
    return paso(
      "scan",
      tabla.info.storage,
      nombre,
      null,
      "recorre la tabla entera",
      stats(paginasTabla, total, total),
    );
  }

  const esRango = where.tipo === "between" || where.operador !== "=";
  const pk = tabla.info.columns.find((c) => c.is_primary_key);

  if (pk && pk.name === where.columna) {
    const op = esRango ? "range_search" : "search";
    const leidas = esRango ? paginas(coincidencias) + 2 : 3;
    return paso(
      op,
      tabla.info.storage,
      nombre,
      where.columna,
      describir(where),
      stats(leidas, coincidencias, coincidencias),
    );
  }

  const indice = tabla.info.indexes.find((i) => i.column === where.columna);

  // Regla 2 del ADR 0002: el hash no recorre intervalos, asi que un rango
  // sobre el cae a scan + filter en vez de usar el indice.
  if (indice && (!esRango || indice.supports_range)) {
    const busqueda = paso(
      esRango ? "index_range" : "index_search",
      indice.structure,
      nombre,
      where.columna,
      describir(where),
      stats(esRango ? 4 : 1, coincidencias, coincidencias),
    );
    // Regla 1: un indice secundario siempre son dos pasos, para que el panel
    // separe las paginas del indice de las de la tabla.
    return paso(
      "fetch",
      tabla.info.storage,
      nombre,
      null,
      `lee ${coincidencias} registros por RID`,
      stats(coincidencias, coincidencias, coincidencias),
      [busqueda],
    );
  }

  const recorrido = paso(
    "scan",
    tabla.info.storage,
    nombre,
    null,
    "recorre la tabla entera",
    stats(paginasTabla, total, total),
  );
  return paso(
    "filter",
    "memory",
    nombre,
    where.columna,
    describir(where),
    stats(0, total, coincidencias),
    [recorrido],
  );
}

export interface Ejecucion {
  columns: string[];
  column_types: import("@/api/types").DataType[];
  rows: CellValue[][];
  plan: Plan;
}

/** Ejecuta la consulta sobre las filas de la tabla y arma su plan. */
export function ejecutar(
  sql: string,
  tabla: TablaFalsa,
  consulta: ConsultaLeida,
): Ejecucion {
  const nombres = tabla.info.columns.map((c) => c.name);

  let filas = tabla.filas;
  if (consulta.where) {
    const indice = nombres.indexOf(consulta.where.columna);
    filas = filas.filter((fila) => cumple(fila[indice], consulta.where!));
  }

  let raiz = rutaDeAcceso(tabla, consulta.where, filas.length);

  if (consulta.orden) {
    const indice = nombres.indexOf(consulta.orden.columna);
    const signo = consulta.orden.descendente ? -1 : 1;
    filas = [...filas].sort((a, b) => signo * comparar(a[indice], b[indice]));

    const runs = Math.max(1, Math.ceil(paginas(filas.length) / 8));
    raiz = paso(
      "sort",
      "external_sort",
      tabla.info.name,
      consulta.orden.columna,
      `k-way merge, ${runs} runs, ${consulta.orden.descendente ? "DESC" : "ASC"}`,
      {
        pages_read: paginas(filas.length),
        pages_written: paginas(filas.length),
        records_examined: filas.length,
        records_returned: filas.length,
      },
      [raiz],
    );
  }

  let columns = nombres;
  let tipos = tabla.tipos;
  if (consulta.columnas) {
    const posiciones = consulta.columnas
      .map((n) => nombres.indexOf(n))
      .filter((i) => i >= 0);
    columns = posiciones.map((i) => nombres[i]);
    tipos = posiciones.map((i) => tabla.tipos[i]);
    filas = filas.map((fila) => posiciones.map((i) => fila[i]));

    raiz = paso(
      "project",
      "memory",
      tabla.info.name,
      null,
      columns.join(", "),
      stats(0, filas.length, filas.length),
      [raiz],
    );
  }

  const suma = totales(raiz);
  return {
    columns,
    column_types: tipos,
    rows: filas,
    plan: {
      query: sql,
      // El planner mide de parsear a devolver, asi que el total supera la suma
      // de los pasos: incluye el parseo y la planificacion.
      time_ms: Number(
        (suma.pages_read * MS_POR_PAGINA + 0.4).toFixed(2),
      ),
      totals: suma,
      root: raiz,
    },
  };
}
