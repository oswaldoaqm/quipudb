/**
 * Datos falsos para trabajar sin motor (criterio 4 del issue #33).
 *
 * Responde como lo haria la API: ejecuta la consulta sobre las filas de
 * `datos.ts` y devuelve su plan, o lanza el error que el motor lanzaria, con
 * el mismo texto y la misma ubicacion.
 */

import { MotorError } from "@/api/errors";
import { ejecutar, leerConsulta } from "@/api/mock/consulta";
import { buscarTabla, MOCK_TABLES } from "@/api/mock/datos";
import type { QueryError, QueryResult, TableInfo } from "@/api/types";

export { MOCK_TABLES };

/** La consulta que el panel ejecuta al abrir, para probar el camino completo. */
export const CONSULTA_DE_PRUEBA =
  "SELECT * FROM alumnos WHERE promedio BETWEEN 15 AND 17";

const LATENCIA_MS = 150;

type Ubicacion = Pick<
  QueryError,
  "line" | "column" | "end_line" | "end_column"
>;

/** Ubicacion 1-based de un tramo, con la convencion de `Span` del parser. */
function ubicarEn(sql: string, indice: number, largo: number): Ubicacion {
  const lineas = sql.slice(0, Math.max(0, indice)).split(/\r\n|\r|\n/);
  const line = lineas.length;
  const column = (lineas[lineas.length - 1]?.length ?? 0) + 1;
  return { line, column, end_line: line, end_column: column + largo };
}

/**
 * Palabras que el parser reconoce pero rechaza.
 *
 * Copia literal de `_UNSUPPORTED_WORDS` en `engine/parser/parser.py`. Una
 * lista corta "de muestra" haria que el panel diera por buenas consultas que
 * el motor rechaza, que es peor que no simular nada.
 */
const FUERA_DEL_SUBCONJUNTO = [
  "ALTER", "AS", "COMMIT", "DISTINCT", "DROP", "EXCEPT", "HAVING", "IN",
  "INDEX", "INTERSECT", "IS", "JOIN", "LIKE", "LIMIT", "NOT", "NULL",
  "OFFSET", "OR", "ROLLBACK", "UNION", "UPDATE", "VIEW",
];

const RESERVADA = new RegExp(`\\b(${FUERA_DEL_SUBCONJUNTO.join("|")})\\b`, "i");
const SENTENCIAS = /^\s*(SELECT|INSERT|DELETE|CREATE|BEGIN|END)\b/i;
// La comilla doble entra aqui a proposito: en este SQL las cadenas van entre
// comillas simples, asi que `"bruno diaz"` es un caracter que el lexer no
// reconoce, no una cadena.
const CARACTER_INVALIDO = /[@#$`~|\\"]/;
const TABLA_DEL_FROM = /\b(?:FROM|INTO|TABLE)\s+([a-zA-Z_][a-zA-Z0-9_]*)/i;

/**
 * Reproduce los errores que el motor produciria, con su ubicacion.
 *
 * Los textos son literales de `Parser._raise_unsupported`, del lexer, del
 * mensaje de sentencia inicial y de `QueryProcessor`, no parafrasis: si el
 * panel mostrara otra redaccion, al conectar la API real cambiaria el mensaje
 * bajo los pies de quien ya se acostumbro a uno.
 */
export function detectarError(sql: string): QueryError | null {
  if (sql.trim() === "") return null;

  // Lo que va entre comillas es dato, no sintaxis: se blanquea conservando las
  // posiciones para que una arroba dentro de un literal no se reporte como
  // error, pero los offsets sigan apuntando al texto original.
  const fuera = sql.replace(/'[^']*'/g, (cita) => " ".repeat(cita.length));

  const invalido = CARACTER_INVALIDO.exec(fuera);
  if (invalido) {
    return {
      error: `caracter inesperado '${invalido[0]}'`,
      kind: "lex",
      ...ubicarEn(sql, invalido.index, 1),
    };
  }

  if (!SENTENCIAS.test(fuera)) {
    const primera = sql.trimStart().split(/\s+/)[0] ?? "";
    return {
      error:
        "se esperaba CREATE TABLE, INSERT INTO, SELECT, DELETE FROM, " +
        "BEGIN TRANSACTION o END TRANSACTION",
      kind: "parse",
      ...ubicarEn(sql, sql.length - sql.trimStart().length, primera.length),
    };
  }

  const reservada = RESERVADA.exec(fuera);
  if (reservada) {
    return {
      error: `${reservada[0].toUpperCase()} no esta soportado por el subconjunto SQL de QuipuDB`,
      kind: "unsupported",
      ...ubicarEn(sql, reservada.index, reservada[0].length),
    };
  }

  const desde = TABLA_DEL_FROM.exec(fuera);
  const nombreTabla = desde?.[1];
  if (desde && nombreTabla && !buscarTabla(nombreTabla)) {
    return {
      error: `la tabla '${nombreTabla}' no existe`,
      kind: "semantic",
      ...ubicarEn(
        sql,
        desde.index + desde[0].indexOf(nombreTabla),
        nombreTabla.length,
      ),
    };
  }

  const tabla = nombreTabla ? buscarTabla(nombreTabla) : undefined;
  // Con `sql` y no con `fuera`: interpretar la consulta necesita los valores
  // de las cadenas, que el blanqueo de arriba convirtio en espacios. Usar
  // `fuera` aqui hacia que `WHERE nombre = 'bruno diaz'` pareciera un WHERE
  // sin valor y se reportara un error de sintaxis inexistente.
  const consulta = leerConsulta(sql);
  if (!tabla || !consulta) return null;

  // Un WHERE que no se logro interpretar NUNCA se ignora. Dejarlo pasar
  // devolveria la tabla entera como si no hubiera condicion: un resultado
  // incorrecto presentado como correcto, que es peor que un error.
  const inicioWhere = fuera.search(/\bWHERE\b/i);
  if (inicioWhere >= 0 && !consulta.where) {
    return {
      error: "se esperaba =, <, <=, >, >= o BETWEEN en WHERE",
      kind: "parse",
      ...ubicarEn(sql, inicioWhere, 5),
    };
  }

  const existentes = tabla.info.columns.map((c) => c.name);
  const mencionadas = [
    ...(consulta.columnas ?? []),
    ...(consulta.where ? [consulta.where.columna] : []),
    ...(consulta.orden ? [consulta.orden.columna] : []),
  ];

  for (const columna of mencionadas) {
    if (!existentes.includes(columna)) {
      return {
        error: `la columna '${columna}' no existe en la tabla '${tabla.info.name}'`,
        kind: "semantic",
        ...ubicarEn(
          sql,
          fuera.toLowerCase().indexOf(columna),
          columna.length,
        ),
      };
    }
  }

  return null;
}

export async function mockExecuteQuery(sql: string): Promise<QueryResult> {
  await new Promise((listo) => setTimeout(listo, LATENCIA_MS));

  const fallo = detectarError(sql);
  if (fallo) throw new MotorError(fallo);

  const consulta = leerConsulta(sql);
  const tabla = consulta ? buscarTabla(consulta.tabla) : undefined;

  // Lo que no es un SELECT comunica su efecto con `affected_rows` y sin plan,
  // igual que el motor: el plan de las escrituras sigue pendiente (ADR 0002).
  if (!consulta || !tabla) {
    return { columns: [], column_types: [], rows: [], affected_rows: 0, plan: null };
  }

  const salida = ejecutar(sql, tabla, consulta);
  return {
    columns: salida.columns,
    column_types: salida.column_types,
    rows: salida.rows,
    affected_rows: 0,
    plan: salida.plan,
  };
}

export async function mockListTables(): Promise<TableInfo[]> {
  await new Promise((listo) => setTimeout(listo, LATENCIA_MS));
  return MOCK_TABLES;
}
