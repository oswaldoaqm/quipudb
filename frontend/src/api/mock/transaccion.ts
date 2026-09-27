/**
 * `BEGIN TRANSACTION` y `END TRANSACTION` sobre los datos falsos.
 *
 * El estado vive en el modulo, no en la sentencia, porque asi funciona el
 * motor: la API mantiene un unico `QueryProcessor` por proceso y la
 * transaccion sobrevive entre peticiones (`engine/api/main.py`). Aqui
 * sobrevive entre ejecuciones del panel, que es lo mismo visto desde el
 * navegador.
 *
 * Se deshacen INSERT y DELETE y nada mas, igual que `Transaction` en
 * `engine/transactions/transaction.py`: un CREATE o un DROP dentro de una
 * transaccion tampoco se revierte en el motor.
 */

import { MotorError } from "@/api/errors";

/** Acciones que devuelven las filas a como estaban, en orden inverso. */
let deshacer: (() => void)[] | null = null;

export function hayTransaccion(): boolean {
  return deshacer !== null;
}

export function esBeginTransaction(sql: string): boolean {
  return /^\s*BEGIN\b/i.test(sql);
}

export function esEndTransaction(sql: string): boolean {
  return /^\s*END\b/i.test(sql);
}

/**
 * Anota como deshacer una escritura.
 *
 * Fuera de una transaccion no hace nada: cada sentencia se confirma sola, que
 * es el autocommit del motor.
 */
export function registrarDeshacer(accion: () => void): void {
  deshacer?.push(accion);
}

export function ejecutarBegin(sql: string): void {
  if (!/^\s*BEGIN\s+TRANSACTION\s*;?\s*$/i.test(sql)) {
    throw MotorError.sinUbicacion("se esperaba TRANSACTION despues de BEGIN");
  }
  if (deshacer !== null) {
    throw MotorError.sinUbicacion(
      "ya hay una transaccion activa: falta un END TRANSACTION",
    );
  }
  deshacer = [];
}

export function ejecutarEnd(sql: string): void {
  if (!/^\s*END\s+TRANSACTION\s*;?\s*$/i.test(sql)) {
    throw MotorError.sinUbicacion("se esperaba TRANSACTION despues de END");
  }
  if (deshacer === null) {
    throw MotorError.sinUbicacion(
      "no hay una transaccion activa: falta un BEGIN TRANSACTION",
    );
  }
  // Confirmar es olvidar como volver atras: las filas ya estan escritas.
  deshacer = null;
}

/**
 * Deshace lo escrito y abandona la transaccion.
 *
 * El motor lo hace antes de relanzar el error de un INSERT o un DELETE que
 * fallo al ejecutarse (`_fail_in_transaction`), para que no quede a medio
 * aplicar. Fuera de una transaccion no hay nada que deshacer.
 */
export function abortarTransaccion(): void {
  if (deshacer === null) return;
  for (const accion of deshacer.reverse()) accion();
  deshacer = null;
}
