/**
 * Deduce del SQL qué hizo una sentencia que no devuelve filas.
 *
 * Hace falta porque la respuesta del motor no lo dice: un `CREATE TABLE` y un
 * `INSERT` que no insertó nada llegan iguales, sin columnas y con
 * `affected_rows` en cero. Mientras `QueryResponse` no lleve la sentencia
 * ejecutada, leer el texto es la única forma de distinguirlos.
 */

const COMENTARIO_BLOQUE = /\/\*[\s\S]*?\*\//g;
const COMENTARIO_LINEA = /--[^\n]*/g;
const CREATE_TABLE = /^\s*CREATE\s+TABLE\s+([a-zA-Z_][a-zA-Z0-9_]*)/i;

/**
 * Nombre de la tabla si la consulta la creó, o null si no fue un CREATE TABLE.
 *
 * `CREATE` va anclado al inicio de la sentencia, no buscado en cualquier
 * posición, para que la palabra dentro de un literal no dispare un falso
 * positivo.
 */
export function tablaCreadaPor(sql: string): string | null {
  const limpio = sql
    .replace(COMENTARIO_BLOQUE, " ")
    .replace(COMENTARIO_LINEA, " ");

  // La API acepta lotes separados por `;`, y el resultado que se muestra es el
  // de la última sentencia; es esa la que hay que mirar.
  const sentencias = limpio.split(";").filter((parte) => parte.trim() !== "");
  const ultima = sentencias[sentencias.length - 1] ?? "";

  return CREATE_TABLE.exec(ultima)?.[1] ?? null;
}
