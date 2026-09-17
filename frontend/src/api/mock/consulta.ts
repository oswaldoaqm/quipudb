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

import type {
  CellValue,
  DataType,
  Plan,
  Stats,
  Step,
  Structure,
} from "@/api/types";
import { type TablaFalsa } from "@/api/mock/datos";

const REGISTROS_POR_PAGINA = 128;
const MS_POR_PAGINA = 0.035;

export type Operador = "=" | "<" | "<=" | ">" | ">=";

export type Condicion =
  | { tipo: "comparacion"; columna: string; operador: Operador; valor: CellValue }
  | { tipo: "between"; columna: string; desde: CellValue; hasta: CellValue };

export type Funcion = "COUNT" | "SUM" | "MIN" | "MAX" | "AVG";

export interface Agregado {
  funcion: Funcion;
  /** `*` solo vale con COUNT, como en el motor. */
  columna: string;
  /** Nombre de salida, con el formato que usa el ejecutor: `AVG_nota`. */
  alias: string;
}

export interface ConsultaLeida {
  /** Columnas simples pedidas; null si es `*` o si solo hay agregados. */
  columnas: string[] | null;
  agregados: Agregado[];
  agrupa: string | null;
  tabla: string;
  where: Condicion | null;
  orden: { columna: string; descendente: boolean } | null;
}

const SELECT_FROM = /\bSELECT\s+(.+?)\s+FROM\s+([a-zA-Z_][a-zA-Z0-9_]*)/is;
const AGREGADO = /^(COUNT|SUM|MIN|MAX|AVG)\s*\(\s*(\*|[a-zA-Z_][a-zA-Z0-9_]*)\s*\)$/i;
const AGRUPA = /\bGROUP\s+BY\s+([a-zA-Z_][a-zA-Z0-9_]*)/i;
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
  const agrupa = AGRUPA.exec(sql);

  const items = lista === "*"
    ? []
    : lista.split(",").map((n) => n.trim()).filter(Boolean);

  const agregados: Agregado[] = [];
  const simples: string[] = [];
  for (const item of items) {
    const llamada = AGREGADO.exec(item);
    if (llamada) {
      const funcion = llamada[1].toUpperCase() as Funcion;
      const columna = llamada[2].toLowerCase();
      // `COUNT(*)` sale como `COUNT_all`, igual que en el ejecutor.
      agregados.push({
        funcion,
        columna,
        alias: `${funcion}_${columna === "*" ? "all" : columna}`,
      });
    } else {
      simples.push(item.toLowerCase());
    }
  }

  return {
    columnas: lista === "*" ? null : simples.length > 0 ? simples : null,
    agregados,
    agrupa: agrupa ? agrupa[1].toLowerCase() : null,
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
  column_types: DataType[];
  rows: CellValue[][];
  plan: Plan;
}

function armarPlan(sql: string, raiz: Step): Plan {
  const suma = totales(raiz);
  return {
    query: sql,
    // El planner mide de parsear a devolver, asi que el total supera la suma
    // de los pasos: incluye el parseo y la planificacion.
    time_ms: Number((suma.pages_read * MS_POR_PAGINA + 0.4).toFixed(2)),
    totals: suma,
    root: raiz,
  };
}

/**
 * Suma compensada de Neumaier, la misma que usa `ExternalGroupBy` del core.
 *
 * Sumar miles de doubles de magnitudes distintas acumula error de redondeo, y
 * el 2.1.6 compara estos resultados contra PostgreSQL, que suma compensado.
 */
function sumaCompensada(valores: number[]): number {
  let suma = 0;
  let compensacion = 0;
  for (const valor of valores) {
    const parcial = suma + valor;
    compensacion +=
      Math.abs(suma) >= Math.abs(valor)
        ? suma - parcial + valor
        : valor - parcial + suma;
    suma = parcial;
  }
  return suma + compensacion;
}

function calcular(
  agregado: Agregado,
  grupo: CellValue[][],
  nombres: string[],
): CellValue {
  if (agregado.funcion === "COUNT" && agregado.columna === "*") {
    return grupo.length;
  }

  const posicion = nombres.indexOf(agregado.columna);
  const valores = grupo
    .map((fila) => fila[posicion])
    .filter((valor) => valor !== null);
  if (valores.length === 0) return null;

  switch (agregado.funcion) {
    case "COUNT":
      return valores.length;
    case "MIN":
      return valores.reduce((a, b) => (comparar(a, b) <= 0 ? a : b));
    case "MAX":
      return valores.reduce((a, b) => (comparar(a, b) >= 0 ? a : b));
    case "SUM":
      return Number(sumaCompensada(valores.map(Number)).toFixed(6));
    case "AVG":
      return Number(
        (sumaCompensada(valores.map(Number)) / valores.length).toFixed(6),
      );
  }
}

/** Un SUM de INT sube a DOUBLE porque un int32 puede desbordar (arquitectura). */
function tipoDeAgregado(agregado: Agregado, tabla: TablaFalsa): DataType {
  if (agregado.funcion === "COUNT") return "INT";
  if (agregado.funcion === "SUM" || agregado.funcion === "AVG") return "DOUBLE";
  const posicion = tabla.info.columns.findIndex(
    (c) => c.name === agregado.columna,
  );
  return posicion >= 0 ? tabla.tipos[posicion] : "DOUBLE";
}

interface Agrupacion {
  columns: string[];
  tipos: DataType[];
  filas: CellValue[][];
}

/** Agrupa por una columna y resuelve los agregados de cada grupo. */
function agrupar(
  filas: CellValue[][],
  nombres: string[],
  tabla: TablaFalsa,
  consulta: ConsultaLeida,
): Agrupacion {
  const clave = nombres.indexOf(consulta.agrupa ?? "");
  const grupos = new Map<string, CellValue[][]>();

  for (const fila of filas) {
    const etiqueta = String(fila[clave]);
    const existente = grupos.get(etiqueta);
    if (existente) existente.push(fila);
    else grupos.set(etiqueta, [fila]);
  }

  return {
    columns: [
      consulta.agrupa ?? "",
      ...consulta.agregados.map((a) => a.alias),
    ],
    tipos: [
      tabla.tipos[clave],
      ...consulta.agregados.map((a) => tipoDeAgregado(a, tabla)),
    ],
    filas: [...grupos.values()].map((grupo) => [
      grupo[0][clave],
      ...consulta.agregados.map((a) => calcular(a, grupo, nombres)),
    ]),
  };
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

  // GROUP BY se resuelve antes del orden: lo que se ordena son los grupos ya
  // formados, no las filas de origen.
  if (consulta.agrupa) {
    const agrupacion = agrupar(filas, nombres, tabla, consulta);
    const antes = filas.length;
    filas = agrupacion.filas;

    raiz = paso(
      "group",
      "external_hash",
      tabla.info.name,
      consulta.agrupa,
      `${filas.length} grupos por ${consulta.agrupa}`,
      {
        pages_read: paginas(antes),
        pages_written: paginas(antes),
        records_examined: antes,
        records_returned: filas.length,
      },
      [raiz],
    );

    const posicionOrden = consulta.orden
      ? agrupacion.columns.indexOf(consulta.orden.columna)
      : -1;
    if (consulta.orden && posicionOrden >= 0) {
      const signo = consulta.orden.descendente ? -1 : 1;
      filas = [...filas].sort(
        (a, b) => signo * comparar(a[posicionOrden], b[posicionOrden]),
      );
      raiz = paso(
        "sort",
        "external_sort",
        tabla.info.name,
        consulta.orden.columna,
        `k-way merge, 1 runs, ${consulta.orden.descendente ? "DESC" : "ASC"}`,
        {
          pages_read: paginas(filas.length),
          pages_written: paginas(filas.length),
          records_examined: filas.length,
          records_returned: filas.length,
        },
        [raiz],
      );
    }

    return {
      columns: agrupacion.columns,
      column_types: agrupacion.tipos,
      rows: filas,
      plan: armarPlan(sql, raiz),
    };
  }

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

  return {
    columns,
    column_types: tipos,
    rows: filas,
    plan: armarPlan(sql, raiz),
  };
}
