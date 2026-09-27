/**
 * Datos falsos para trabajar sin motor (criterio 4 del issue #33).
 *
 * Responde como lo haria la API: ejecuta la consulta sobre las filas de
 * `datos.ts` y devuelve su plan, o lanza el error que el motor lanzaria, con
 * el mismo texto y la misma ubicacion.
 */

import { MotorError } from "@/api/errors";
import {
  cumple,
  ejecutar,
  leerConsulta,
  REFERENCIA,
  rutaDeAcceso,
  type Condicion,
  type ConsultaLeida,
} from "@/api/mock/consulta";
import { buscarTabla, catalogo, type TablaFalsa } from "@/api/mock/datos";
import {
  ejecutarCreateIndex,
  ejecutarCreateTable,
  ejecutarDropTable,
  esCreate,
  esCreateIndex,
  esDropTable,
} from "@/api/mock/ddl";
import {
  ejecutarDelete,
  ejecutarInsert,
  esDelete,
  esInsert,
} from "@/api/mock/dml";
import {
  alcanceDeTabla,
  hayJoin,
  leerJoin,
  materializar,
  pasoDeJoin,
  resolverConsulta,
} from "@/api/mock/juntar";
import {
  ejecutarBegin,
  ejecutarEnd,
  esBeginTransaction,
  esEndTransaction,
} from "@/api/mock/transaccion";
import { ubicarEn } from "@/api/mock/ubicacion";
import type { QueryError, QueryResult, TableInfo } from "@/api/types";

/** La consulta que el panel ejecuta al abrir, para probar el camino completo. */
export const CONSULTA_DE_PRUEBA =
  "SELECT * FROM alumnos WHERE promedio BETWEEN 15 AND 17";

const LATENCIA_MS = 150;

/**
 * Palabras que el parser reconoce pero rechaza.
 *
 * Copia literal de `_UNSUPPORTED_WORDS` en `engine/parser/parser.py`. Una
 * lista corta "de muestra" haria que el panel diera por buenas consultas que
 * el motor rechaza, que es peor que no simular nada.
 */
const FUERA_DEL_SUBCONJUNTO = [
  "ALTER", "AS", "COMMIT", "DISTINCT", "EXCEPT", "HAVING", "IN", "INTERSECT",
  "IS", "LIKE", "NOT", "NULL", "OFFSET", "OR", "ROLLBACK", "UNION",
  "UPDATE", "VIEW",
];

/** Texto exacto con el que el parser enumera las sentencias que reconoce. */
const SENTENCIAS_ESPERADAS =
  "se esperaba CREATE TABLE, CREATE INDEX, DROP TABLE, INSERT INTO, SELECT, " +
  "EXPLAIN, DELETE FROM, BEGIN TRANSACTION o END TRANSACTION";

const RESERVADA = new RegExp(`\\b(${FUERA_DEL_SUBCONJUNTO.join("|")})\\b`, "i");
const SENTENCIAS =
  /^\s*(SELECT|INSERT|DELETE|CREATE|DROP|EXPLAIN|BEGIN|END)\b/i;

/**
 * Una condicion seguida de `AND`.
 *
 * `Parser._condition()` lee una sola condicion y despues exige el final de la
 * sentencia, asi que una conjuncion no llega a ejecutarse. El `AND` de un
 * BETWEEN no cuenta: ahi forma parte de la propia condicion, y por eso el
 * patron exige un operador de comparacion antes.
 */
const CONJUNCION = new RegExp(
  `\\bWHERE\\s+${REFERENCIA}\\s*(?:<=|>=|=|<|>)\\s*` +
    "(?:'[^']*'|-?[\\d.]+|true|false)\\s+AND\\b",
  "i",
);
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

  // Un CREATE nombra una tabla que todavia no existe, asi que las
  // comprobaciones de abajo —pensadas para consultas sobre tablas existentes—
  // lo rechazarian. Sus propios errores los reporta `ddl.ts` al ejecutarlo.
  if (esCreate(sql)) return null;

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
      error: SENTENCIAS_ESPERADAS,
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

  const limit = /\bLIMIT\b/i.exec(fuera);
  if (limit) {
    let inicio = limit.index + limit[0].length;
    while (/\s/.test(fuera[inicio] ?? "")) inicio += 1;
    const token = /^[^\s;]+/.exec(fuera.slice(inicio))?.[0] ?? "";
    if (/^-\d+$/.test(token)) {
      return {
        error: "LIMIT no admite valores negativos",
        kind: "parse",
        ...ubicarEn(sql, inicio, token.length),
      };
    }
    if (!/^\d+$/.test(token)) {
      return {
        error: "LIMIT requiere un entero no negativo",
        kind: "parse",
        ...ubicarEn(sql, inicio, token.length),
      };
    }
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

  // Antes de leer la consulta: una conjuncion tambien invalida un DELETE, y
  // `leerConsulta` solo entiende SELECT, asi que ahi ya seria tarde.
  const conjuncion = CONJUNCION.exec(fuera);
  if (conjuncion) {
    const posicionAnd = fuera.toUpperCase().indexOf("AND", conjuncion.index);
    return {
      error: "se esperaba el final de la sentencia",
      kind: "parse",
      ...ubicarEn(sql, posicionAnd, 3),
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

  // Un elemento de la proyeccion con dos palabras o con parentesis que no
  // forman un agregado valido: el parser corta ahi porque espera una coma o
  // FROM. Es el caso de `SELECT MAX(promedio) nombre` sin la coma.
  const malformado = (consulta.columnas ?? []).find((c) => /[\s()]/.test(c));
  if (malformado) {
    return {
      error: "se esperaba FROM despues de la proyeccion",
      kind: "parse",
      ...ubicarEn(
        sql,
        fuera.toLowerCase().indexOf(malformado),
        malformado.length,
      ),
    };
  }

  // Los agregados existen en el motor, pero `semantic.py` los exige dentro de
  // un GROUP BY. Sin esto el mock los tomaria por nombres de columna y diria
  // que no existen, que es una pista falsa.
  if (consulta.agregados.length > 0 && !consulta.agrupa) {
    const primero = consulta.agregados[0];
    return {
      error: "las funciones de agregado requieren una clausula GROUP BY",
      kind: "semantic",
      ...ubicarEn(
        sql,
        fuera.toUpperCase().indexOf(primero.funcion),
        primero.funcion.length,
      ),
    };
  }

  // Que cada nombre exista y no sea ambiguo lo decide `resolverConsulta`, con
  // las mismas reglas de `_resolve_column`: una referencia calificada solo
  // mira su tabla, una sin calificar mira todo el alcance. Lanza en vez de
  // devolver, asi que aqui se traduce a la forma que espera el panel.
  try {
    const alcance = hayJoin(sql)
      ? leerJoin(sql).alcance
      : alcanceDeTabla(tabla);
    resolverConsulta(consulta, alcance, sql);
  } catch (fallo) {
    if (fallo instanceof MotorError) return comoQueryError(fallo);
    throw fallo;
  }

  return null;
}

/** El error que lanza el mock, en la forma en la que viaja por la API. */
function comoQueryError(fallo: MotorError): QueryError {
  return {
    error: fallo.message,
    kind: fallo.kind,
    line: fallo.line,
    column: fallo.column,
    end_line: fallo.endLine,
    end_column: fallo.endColumn,
  };
}

/** Parte un script en sentencias, sin cortar por un `;` dentro de una cadena. */
function partirScript(sql: string): string[] {
  const sentencias: string[] = [];
  let actual = "";
  let enCadena = false;

  for (const caracter of sql) {
    if (caracter === "'") enCadena = !enCadena;
    if (caracter === ";" && !enCadena) {
      if (actual.trim() !== "") sentencias.push(actual.trim());
      actual = "";
    } else {
      actual += caracter;
    }
  }
  if (actual.trim() !== "") sentencias.push(actual.trim());
  return sentencias;
}

/**
 * Ejecuta el script completo.
 *
 * Con varias sentencias hace lo mismo que `QueryProcessor.execute`: las aplica
 * en orden, suma `affected_rows`, y devuelve las filas y el plan de la ultima.
 */
export async function mockExecuteQuery(sql: string): Promise<QueryResult> {
  await new Promise((listo) => setTimeout(listo, LATENCIA_MS));

  const sentencias = partirScript(sql);
  if (sentencias.length > 1) {
    const resultados: QueryResult[] = [];
    for (const sentencia of sentencias) {
      resultados.push(ejecutarUna(sentencia));
    }
    const ultima = resultados[resultados.length - 1];
    return {
      ...ultima,
      affected_rows: resultados.reduce(
        (total, resultado) => total + resultado.affected_rows,
        0,
      ),
    };
  }

  return ejecutarUna(sql);
}

const SIN_FILAS: QueryResult = {
  columns: [],
  column_types: [],
  rows: [],
  affected_rows: 0,
  plan: null,
};

/** `EXPLAIN [ANALYZE] SELECT ...`: el parser solo admite SELECT detras. */
const EXPLAIN = /^\s*EXPLAIN\s+(?:ANALYZE\s+)?([\s\S]+)$/i;

function ejecutarUna(sql: string): QueryResult {
  const explain = EXPLAIN.exec(sql);
  if (explain) {
    const salida = ejecutarUna(explain[1]);
    // Sin ANALYZE se devuelve el plan pero no las filas, como en PostgreSQL.
    return /^\s*EXPLAIN\s+ANALYZE\b/i.test(sql)
      ? salida
      : { ...salida, columns: [], column_types: [], rows: [] };
  }

  const fallo = detectarError(sql);
  if (fallo) throw new MotorError(fallo);

  if (esBeginTransaction(sql)) {
    ejecutarBegin(sql);
    return SIN_FILAS;
  }

  if (esEndTransaction(sql)) {
    ejecutarEnd(sql);
    return SIN_FILAS;
  }

  if (esDropTable(sql)) {
    ejecutarDropTable(sql);
    return SIN_FILAS;
  }

  // Todo CREATE pasa por aqui, tambien el que esta mal escrito: si cayera al
  // final se anunciaria como "0 filas afectadas" en vez de decir que no se
  // entendio. Cada rama lanza su propio MotorError.
  if (esCreate(sql)) {
    if (esCreateIndex(sql)) ejecutarCreateIndex(sql);
    else ejecutarCreateTable(sql);
    return SIN_FILAS;
  }

  // El INSERT comunica su efecto con `affected_rows` y sin plan, igual que el
  // motor: el plan de las escrituras sigue pendiente (ADR 0002).
  if (esInsert(sql)) {
    return { ...SIN_FILAS, affected_rows: ejecutarInsert(sql) };
  }

  if (esDelete(sql)) {
    return { ...SIN_FILAS, affected_rows: ejecutarDelete(sql) };
  }

  const consultaCruda = leerConsulta(sql);
  if (consultaCruda && hayJoin(sql)) return ejecutarJoin(sql, consultaCruda);

  const tabla = consultaCruda ? buscarTabla(consultaCruda.tabla) : undefined;
  const consulta =
    consultaCruda && tabla
      ? resolverConsulta(consultaCruda, alcanceDeTabla(tabla), sql)
      : null;

  // Aqui ya solo quedan SELECT, y uno que no se dejo leer es un SELECT sin
  // FROM. Nunca se devuelve "0 filas afectadas" en silencio: eso haria pasar
  // por ejecutada una sentencia que el motor habria rechazado.
  if (!consulta || !tabla) {
    throw new MotorError({
      error: "se esperaba FROM despues de la proyeccion",
      kind: "parse",
      ...ubicarEn(sql, 0, sql.trim().length),
    });
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

/**
 * `SELECT ... FROM a JOIN b ON ...`.
 *
 * El predicado baja a la hoja a la que pertenece su columna y el otro lado se
 * recorre entero, que es lo que hace `_optimize_source`: filtrar antes de
 * juntar es mas barato y cambia la ruta de acceso de ese lado. Con las filas
 * ya juntadas, el ORDER BY y la proyeccion los resuelve `ejecutar` sobre la
 * tabla sintetica.
 */
function ejecutarJoin(sql: string, consultaCruda: ConsultaLeida): QueryResult {
  const join = leerJoin(sql);
  const consulta = resolverConsulta(consultaCruda, join.alcance, sql);
  const corte = join.izquierda.info.columns.length;

  const posicion = consulta.where
    ? join.alcance.salida.indexOf(consulta.where.columna)
    : -1;
  // La condicion con el nombre que la columna tiene en SU tabla: la hoja no
  // sabe nada del prefijo que le puso el esquema de salida.
  const local: Condicion | null =
    consulta.where && posicion >= 0
      ? { ...consulta.where, columna: join.alcance.visible[posicion].columna }
      : null;

  const enIzquierda = posicion >= 0 && posicion < corte;
  const lado = (tabla: TablaFalsa, condicion: Condicion | null) => {
    const indice = condicion
      ? tabla.info.columns.findIndex((c) => c.name === condicion.columna)
      : -1;
    const filas =
      condicion && indice >= 0
        ? tabla.filas.filter((fila) => cumple(fila[indice], condicion))
        : tabla.filas;
    return { filas, paso: rutaDeAcceso(tabla, condicion, filas.length) };
  };

  const izquierda = lado(join.izquierda, enIzquierda ? local : null);
  const derecha = lado(join.derecha, posicion >= corte ? local : null);

  const tabla = materializar(join, izquierda.filas, derecha.filas);
  const raiz = pasoDeJoin(
    join,
    izquierda.paso,
    derecha.paso,
    posicion < corte,
  );

  const salida = ejecutar(sql, tabla, consulta, raiz);
  return {
    columns: salida.columns,
    column_types: salida.column_types,
    rows: salida.rows,
    affected_rows: 0,
    plan: salida.plan,
  };
}

export { mockLoadCsv } from "@/api/mock/csv";

export async function mockListTables(): Promise<TableInfo[]> {
  await new Promise((listo) => setTimeout(listo, LATENCIA_MS));
  return catalogo();
}
