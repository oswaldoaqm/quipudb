/**
 * `INSERT INTO` sobre las tablas falsas.
 *
 * Una fila por sentencia, que es lo que acepta el motor. Valida lo mismo que
 * él —cantidad de valores, tipo de cada uno y clave primaria libre— y con sus
 * mismos mensajes, tomados de `engine/parser/semantic.py` y de
 * `core/src/storage/heap_file.cpp`.
 */

import { MotorError } from "@/api/errors";
import { buscarTabla, type TablaFalsa } from "@/api/mock/datos";
import { ubicarEn } from "@/api/mock/ubicacion";
import type { CellValue, ColumnInfo } from "@/api/types";

const INSERT =
  /^\s*INSERT\s+INTO\s+([a-zA-Z_]\w*)\s+VALUES\s*\(([\s\S]*)\)\s*;?\s*$/i;

const INT32_MIN = -2_147_483_648;
const INT32_MAX = 2_147_483_647;

export function esInsert(sql: string): boolean {
  return /^\s*INSERT\s+INTO\b/i.test(sql);
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
    return texto.slice(1, -1);
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
  const partes = INSERT.exec(sql);
  if (!partes) {
    throw new MotorError({
      error: "se esperaba VALUES seguido de la lista de valores",
      kind: "parse",
      ...ubicarEn(sql, 0, 6),
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
      throw new MotorError({
        error: `la clave primaria ya existe en ${tabla.info.name}`,
        kind: "semantic",
        ...ubicarEn(sql, posicionTabla, nombre.length),
      });
    }
  }

  tabla.filas.push(fila);
  tabla.info.record_count = tabla.filas.length;
  return 1;
}
