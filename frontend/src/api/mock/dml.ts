/**
 * `INSERT INTO` sobre las tablas falsas.
 *
 * Una fila por sentencia, que es lo que acepta el motor. Valida lo mismo que
 * él —cantidad de valores, tipo de cada uno y clave primaria libre— y con sus
 * mismos mensajes, tomados de `engine/parser/semantic.py` y de
 * `core/src/storage/heap_file.cpp`.
 */

import { MotorError } from "@/api/errors";
import { cumple, leerCondicion } from "@/api/mock/consulta";
import { buscarTabla, type TablaFalsa } from "@/api/mock/datos";
import { abortarTransaccion, registrarDeshacer } from "@/api/mock/transaccion";
import { ubicarEn } from "@/api/mock/ubicacion";
import type { CellValue, ColumnInfo } from "@/api/types";

const INSERT =
  /^\s*INSERT\s+INTO\s+([a-zA-Z_]\w*)\s+VALUES\s*\(([\s\S]*)\)\s*;?\s*$/i;

const INT32_MIN = -2_147_483_648;
const INT32_MAX = 2_147_483_647;

export function esInsert(sql: string): boolean {
  return /^\s*INSERT\b/i.test(sql);
}

export function esDelete(sql: string): boolean {
  return /^\s*DELETE\b/i.test(sql);
}

const DELETE_FROM = /^\s*DELETE\s+FROM\s+([a-zA-Z_]\w*)/i;

/** Borra las filas que cumplen la condicion y devuelve cuantas quito. */
export function ejecutarDelete(sql: string): number {
  const partes = DELETE_FROM.exec(sql);
  if (!partes) {
    throw new MotorError({
      error: "se esperaba FROM despues de DELETE",
      kind: "parse",
      ...ubicarEn(sql, 0, 6),
    });
  }

  const nombre = partes[1];
  const posicionTabla = sql.toLowerCase().indexOf(nombre.toLowerCase());
  const tabla = buscarTabla(nombre);

  if (!tabla) {
    throw new MotorError({
      error: `la tabla '${nombre}' no existe`,
      kind: "semantic",
      ...ubicarEn(sql, posicionTabla, nombre.length),
    });
  }

  // El parser lo exige: un DELETE sin condicion vaciaria la tabla entera.
  const condicion = leerCondicion(sql);
  if (!condicion) {
    throw new MotorError({
      error: "DELETE requiere una clausula WHERE",
      kind: "parse",
      ...ubicarEn(sql, 0, sql.trim().length),
    });
  }

  const posicionColumna = tabla.info.columns.findIndex(
    (c) => c.name === condicion.columna,
  );
  if (posicionColumna < 0) {
    throw new MotorError({
      // "en la consulta sobre" y no "en la tabla": el DELETE resuelve su
      // WHERE con el mismo `_resolve_column` que un SELECT, y ese es el texto
      // que usa cuando la referencia no lleva calificador.
      error: `la columna '${condicion.columna}' no existe en la consulta sobre '${tabla.info.name}'`,
      kind: "semantic",
      ...ubicarEn(
        sql,
        sql.toLowerCase().indexOf(condicion.columna),
        condicion.columna.length,
      ),
    });
  }

  const quedan = tabla.filas.filter(
    (fila) => !cumple(fila[posicionColumna], condicion),
  );
  const borradas = tabla.filas.length - quedan.length;
  const antes = [...tabla.filas];

  tabla.filas.length = 0;
  tabla.filas.push(...quedan);
  tabla.info.record_count = tabla.filas.length;

  // Guarda las filas de antes por si la transaccion se aborta. Es una copia
  // del arreglo, no de cada fila: las filas no se modifican en su sitio.
  registrarDeshacer(() => {
    tabla.filas.length = 0;
    tabla.filas.push(...antes);
    tabla.info.record_count = tabla.filas.length;
  });

  return borradas;
}

/** Separa por comas sin partir las que van dentro de una cadena. */
function partirValores(lista: string): string[] {
  const partes: string[] = [];
  let actual = "";
  let enCadena = false;

  for (const caracter of lista) {
    if (caracter === "'") enCadena = !enCadena;
    if (caracter === "," && !enCadena) {
      partes.push(actual.trim());
      actual = "";
    } else {
      actual += caracter;
    }
  }
  if (actual.trim() !== "") partes.push(actual.trim());
  return partes;
}

/** Cómo nombra el motor al tipo de un literal cuando no calza con la columna. */
function tipoDelLiteral(texto: string): string {
  if (texto.startsWith("'")) return "VARCHAR";
  if (/^(true|false)$/i.test(texto)) return "BOOL";
  if (/^-?\d+$/.test(texto)) return "INT";
  if (/^-?\d*\.\d+$/.test(texto)) return "DOUBLE";
  return "desconocido";
}

function convertir(
  texto: string,
  columna: ColumnInfo,
  sql: string,
  posicion: number,
): CellValue {
  const falla = (mensaje: string): never => {
    throw new MotorError({
      error: `columna ${columna.name}: ${mensaje}`,
      kind: "semantic",
      ...ubicarEn(sql, posicion, texto.length),
    });
  };

  const recibido = tipoDelLiteral(texto);
  const esperado = columna.type;

  if (esperado === "VARCHAR") {
    if (recibido !== "VARCHAR") {
      falla(`se esperaba VARCHAR y se recibio ${recibido}`);
    }
    const contenido = texto.slice(1, -1);
    // El motor mide en bytes UTF-8, no en caracteres: una tilde ocupa dos.
    const bytes = new TextEncoder().encode(contenido).length;
    if (columna.size !== null && bytes > columna.size) {
      falla(`el texto ocupa ${bytes} bytes y supera VARCHAR(${columna.size})`);
    }
    return contenido;
  }

  if (esperado === "BOOL") {
    if (recibido !== "BOOL") {
      falla(`se esperaba BOOL y se recibio ${recibido}`);
    }
    return /^true$/i.test(texto);
  }

  if (esperado === "INT" || esperado === "DATE") {
    if (recibido !== "INT") {
      falla(`se esperaba ${esperado} y se recibio ${recibido}`);
    }
    const entero = Number(texto);
    if (entero < INT32_MIN || entero > INT32_MAX) {
      falla(`${texto} esta fuera del rango INT de 32 bits`);
    }
    return entero;
  }

  // DOUBLE admite enteros: el motor los promueve segun el esquema.
  if (recibido !== "DOUBLE" && recibido !== "INT") {
    falla(`se esperaba DOUBLE y se recibio ${recibido}`);
  }
  return Number(texto);
}

/** Inserta la fila y devuelve cuántas entraron. */
export function ejecutarInsert(sql: string): number {
  if (!/^\s*INSERT\s+INTO\b/i.test(sql)) {
    throw new MotorError({
      error: "se esperaba INTO despues de INSERT",
      kind: "parse",
      ...ubicarEn(sql, 0, 6),
    });
  }

  const partes = INSERT.exec(sql);
  if (!partes) {
    throw new MotorError({
      error: "se esperaba VALUES despues del nombre de la tabla",
      kind: "parse",
      ...ubicarEn(sql, 0, sql.trim().length),
    });
  }

  const [, nombre, listaValores] = partes;
  const tabla: TablaFalsa | undefined = buscarTabla(nombre);
  const posicionTabla = sql.toLowerCase().indexOf(nombre.toLowerCase());

  if (!tabla) {
    throw new MotorError({
      error: `la tabla '${nombre}' no existe`,
      kind: "semantic",
      ...ubicarEn(sql, posicionTabla, nombre.length),
    });
  }

  const valores = partirValores(listaValores);
  const columnas = tabla.info.columns;

  if (valores.length !== columnas.length) {
    throw new MotorError({
      error:
        `INSERT tiene ${valores.length} valores y la tabla ` +
        `${tabla.info.name} requiere ${columnas.length}`,
      kind: "semantic",
      ...ubicarEn(sql, 0, sql.trim().length),
    });
  }

  const fila = valores.map((texto, indice) =>
    convertir(texto, columnas[indice], sql, sql.indexOf(texto)),
  );

  // La clave primaria es unica: el duplicado lanza DuplicateKey en el core.
  const posicionClave = columnas.findIndex((c) => c.is_primary_key);
  if (posicionClave >= 0) {
    const clave = fila[posicionClave];
    if (tabla.filas.some((existente) => existente[posicionClave] === clave)) {
      // El duplicado lo detecta el core al escribir, no el validador, y el
      // motor deshace la transaccion antes de relanzarlo
      // (`_fail_in_transaction` en `executor/processor.py`). Los errores de
      // tipo de mas arriba se detectan antes de tocar el disco y la dejan viva.
      abortarTransaccion();
      throw new MotorError({
        error: `la clave primaria ya existe en ${tabla.info.name}`,
        kind: "semantic",
        ...ubicarEn(sql, posicionTabla, nombre.length),
      });
    }
  }

  tabla.filas.push(fila);
  tabla.info.record_count = tabla.filas.length;

  // Se quita por identidad y no por posicion: una sentencia posterior de la
  // misma transaccion pudo haber cambiado el largo del arreglo.
  registrarDeshacer(() => {
    const posicion = tabla.filas.indexOf(fila);
    if (posicion < 0) return;
    tabla.filas.splice(posicion, 1);
    tabla.info.record_count = tabla.filas.length;
  });

  return 1;
}
