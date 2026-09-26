/**
 * Deduce del SQL qué hizo una sentencia que no devuelve filas.
 *
 * Hace falta porque la respuesta del motor no lo dice: un `CREATE TABLE`, un
 * `INSERT` y un `DELETE` llegan iguales, sin columnas y solo con
 * `affected_rows`. Mientras `QueryResponse` no lleve la sentencia ejecutada,
 * leer el texto es la única forma de saber qué anunciar.
 */

export type Efecto =
  | { clase: "creacion"; tabla: string }
  | { clase: "insercion" }
  | { clase: "otra" };

const COMENTARIO_BLOQUE = /\/\*[\s\S]*?\*\//g;
const COMENTARIO_LINEA = /--[^\n]*/g;
const CREATE_TABLE = /^\s*CREATE\s+TABLE\s+([a-zA-Z_][a-zA-Z0-9_]*)/i;
const INSERT_INTO = /^\s*INSERT\s+INTO\s+[a-zA-Z_][a-zA-Z0-9_]*/i;

/**
 * La última sentencia del texto, sin comentarios.
 *
 * La API acepta lotes separados por `;`, y el resultado que se muestra es el
 * de la última; es esa la que hay que mirar.
 */
function ultimaSentencia(sql: string): string {
  const limpio = sql
    .replace(COMENTARIO_BLOQUE, " ")
    .replace(COMENTARIO_LINEA, " ");
  const partes = limpio.split(";").filter((parte) => parte.trim() !== "");
  return partes[partes.length - 1] ?? "";
}

/**
 * Qué hizo la consulta, para que el panel lo anuncie con el texto correcto.
 *
 * Las palabras van ancladas al inicio de la sentencia, no buscadas en
 * cualquier posición, para que una de ellas dentro de un literal no dispare
 * un falso positivo.
 */
export function efectoDe(sql: string): Efecto {
  const ultima = ultimaSentencia(sql);

  const creacion = CREATE_TABLE.exec(ultima);
  if (creacion) return { clase: "creacion", tabla: creacion[1] };

  if (INSERT_INTO.test(ultima)) return { clase: "insercion" };

  return { clase: "otra" };
}
