/**
 * Carga de un CSV sobre las tablas falsas.
 *
 * Es el espejo de `engine/executor/bulk_load.py`: el mismo reconocimiento del
 * separador, el mismo emparejamiento de la cabecera y los mismos mensajes,
 * literales y no parafraseados. Sin esto la carga no se puede demostrar sin
 * el motor compilado, que es justo lo que el modo de datos falsos existe para
 * evitar.
 *
 * Lo que NO se reproduce es el streaming: aqui el archivo entra entero en
 * memoria porque el navegador ya lo tiene asi. Lo que si se reproduce es que
 * la pestana no se congele, cediendo el hilo cada tanto (criterio 5 del
 * issue #113).
 */

import { MotorError } from "@/api/errors";
import { buscarTabla, type TablaFalsa } from "@/api/mock/datos";
import { hayTransaccion } from "@/api/mock/transaccion";
import type {
  CellValue,
  ColumnInfo,
  LoadResult,
  LoadRowError,
} from "@/api/types";

/** Copia de `MAX_REPORTED_ERRORS`: el resto solo se cuenta. */
const MAXIMO_ERRORES = 100;

/**
 * Separadores que se reconocen solos.
 *
 * El punto y coma es el que usa Excel con la configuracion regional de Peru,
 * porque ahi la coma es el separador decimal. El orden importa: si dos
 * empatan gana el primero, igual que el `max()` de Python.
 */
const SEPARADORES = [",", ";", "\t"];

const ENTERO = /^[+-]?[0-9](?:_?[0-9])*$/;
const FECHA = /^[0-9]{4}-[0-9]{2}-[0-9]{2}$/;

/**
 * Lo que acepta `float()` de Python, que es quien convierte alla.
 *
 * Hace falta un patron y no basta con `Number()`: este ultimo acepta `0x10`,
 * `0b11` y `0o17` como numeros, y Python los rechaza. Dejarlo pasar cargaria
 * un 16 donde el motor habria dado un error, que es peor que no simular nada.
 * Los guiones bajos entre digitos si los acepta Python (`1_000.5`).
 */
const DIGITOS = "[0-9](?:_?[0-9])*";
const DECIMAL = new RegExp(
  `^[+-]?(?:${DIGITOS}(?:\\.(?:${DIGITOS})?)?|\\.${DIGITOS})(?:[eE][+-]?${DIGITOS})?$`,
);

/** `float()` tambien acepta estos, y despues el motor los rechaza por infinitos. */
const NO_FINITO = /^[+-]?(?:inf|infinity|nan)$/i;
const VERDADERO = new Set(["true", "1"]);
const FALSO = new Set(["false", "0"]);

const INT32_MIN = -2_147_483_648;
const INT32_MAX = 2_147_483_647;

/** Cuantas filas se procesan antes de devolver el hilo al navegador. */
const FILAS_POR_TANDA = 2_000;

/** Como `_lista`: los nombres entre comillas simples y separados por coma. */
function lista(nombres: string[]): string {
  return nombres.map((n) => `'${n}'`).join(", ");
}

/** El error que aborta la carga entera, sin insertar nada. */
function fallaDeCarga(mensaje: string, linea: number | null = null): never {
  throw new MotorError({
    error: mensaje,
    kind: null,
    line: linea,
    column: null,
    end_line: null,
    end_column: null,
  });
}

/**
 * Decide la codificacion como `detect_encoding`.
 *
 * "Guardar como CSV" en un Excel de Windows en espanol escribe cp1252, no
 * UTF-8: una "n" con tilde es el byte 0xF1 suelto. Rechazar esos archivos
 * obligaria a cada usuario a saber que es una codificacion. Pero solo se
 * acepta si el archivo no tiene NINGUNA secuencia UTF-8 de varios bytes: si
 * las tiene, es UTF-8 con un byte roto, y leerlo como cp1252 convertiria
 * cada tilde en basura sin avisar.
 */
function decodificar(bytes: ArrayBuffer): {
  texto: string;
  encoding: LoadResult["encoding"];
} {
  const crudos = new Uint8Array(bytes);
  try {
    const texto = new TextDecoder("utf-8", { fatal: true }).decode(crudos);
    // El BOM que escribe Excel no es parte de la primera cabecera.
    return { texto: texto.replace(/^﻿/, ""), encoding: "utf-8" };
  } catch {
    if (tieneUtf8DeVariosBytes(crudos)) {
      fallaDeCarga(
        "el archivo no es UTF-8 valido; guardalo como UTF-8 y vuelve a subirlo",
        lineaDelPrimerByteInvalido(crudos),
      );
    }
    return {
      texto: new TextDecoder("windows-1252").decode(crudos),
      encoding: "cp1252",
    };
  }
}

/** Un byte de arranque de secuencia UTF-8 seguido de su continuacion. */
function tieneUtf8DeVariosBytes(bytes: Uint8Array): boolean {
  for (let i = 0; i < bytes.length - 1; i++) {
    const actual = bytes[i];
    const siguiente = bytes[i + 1];
    const esArranque = actual >= 0xc2 && actual <= 0xf4;
    const esContinuacion = (siguiente & 0xc0) === 0x80;
    if (esArranque && esContinuacion) return true;
  }
  return false;
}

function lineaDelPrimerByteInvalido(bytes: Uint8Array): number {
  const decodificador = new TextDecoder("utf-8", { fatal: true });
  let linea = 1;
  for (let i = 0; i < bytes.length; i++) {
    if (bytes[i] === 0x0a) linea++;
    try {
      decodificador.decode(bytes.subarray(0, i + 1), { stream: true });
    } catch {
      return linea;
    }
  }
  return linea;
}

/**
 * Parte el texto en filas respetando las comillas.
 *
 * Un campo entre comillas puede tener el separador, saltos de linea y
 * comillas escritas como `""`. Cada fila lleva la linea donde EMPIEZA: un
 * texto de varias lineas se busca por la primera, no por la ultima.
 */
function partirFilas(
  texto: string,
  separador: string,
): { campos: string[]; linea: number }[] {
  const filas: { campos: string[]; linea: number }[] = [];
  let campos: string[] = [];
  let actual = "";
  let entreComillas = false;
  let linea = 1;
  let lineaDeLaFila = 1;
  let hayContenido = false;

  const cerrarFila = () => {
    campos.push(actual);
    // Una linea en blanco no es una fila: el lector de Python la saltea.
    if (hayContenido) filas.push({ campos, linea: lineaDeLaFila });
    campos = [];
    actual = "";
    hayContenido = false;
  };

  for (let i = 0; i < texto.length; i++) {
    const caracter = texto[i];

    if (entreComillas) {
      if (caracter === '"') {
        if (texto[i + 1] === '"') {
          actual += '"';
          i++;
        } else entreComillas = false;
      } else {
        if (caracter === "\n") linea++;
        actual += caracter;
      }
      continue;
    }

    // Una comilla solo abre campo si es el PRIMER caracter: el lector de
    // Python solo entra en modo comillado desde el inicio del campo, asi que
    // en `a"b` la comilla es un caracter mas y la fila tiene tres campos.
    if (caracter === '"' && actual === "") {
      entreComillas = true;
      hayContenido = true;
    } else if (caracter === separador) {
      campos.push(actual);
      actual = "";
      hayContenido = true;
    } else if (caracter === "\r") {
      // Se ignora: el salto lo marca el \n que viene detras.
    } else if (caracter === "\n") {
      cerrarFila();
      linea++;
      lineaDeLaFila = linea;
    } else {
      actual += caracter;
      if (caracter.trim() !== "") hayContenido = true;
    }
  }

  if (hayContenido || campos.length > 0) cerrarFila();
  return filas;
}

/**
 * La primera linea con algo escrito, que es donde esta la cabecera.
 *
 * `read_header` descarta las lineas en blanco del inicio antes de mirar nada,
 * asi que el separador tambien tiene que decidirse sobre esa y no sobre la
 * primera del archivo: con un `\n` al principio, una cabecera separada por
 * punto y coma se leia como una sola columna.
 */
function primeraLineaConTexto(texto: string): string {
  for (const linea of texto.split("\n")) {
    if (linea.trim() !== "") return linea;
  }
  return "";
}

/** El separador con el que la cabecera da mas columnas; empate, el primero. */
function elegirSeparador(primeraLinea: string): string {
  let mejor = SEPARADORES[0];
  let columnas = 0;
  for (const separador of SEPARADORES) {
    const cuantas = partirFilas(primeraLinea, separador)[0]?.campos.length ?? 0;
    if (cuantas > columnas) {
      columnas = cuantas;
      mejor = separador;
    }
  }
  return mejor;
}

/**
 * Por cada columna del esquema, su posicion en el CSV.
 *
 * El orden del CSV es libre, pero tienen que estar todas las columnas y
 * ninguna mas. Un nombre se busca primero exacto y, si no esta, sin
 * distinguir mayusculas —Excel suele capitalizar las cabeceras—. Se ignoran
 * los espacios de los bordes y las columnas sin nombre del final, que deja
 * Excel cuando una fila termina en separador.
 */
function emparejarCabecera(
  cabecera: string[],
  tabla: TablaFalsa,
  linea: number,
): number[] {
  const nombres = cabecera.map((n) => n.trim());
  while (nombres.length > 0 && nombres[nombres.length - 1] === "") {
    nombres.pop();
  }

  if (nombres.length === 0) fallaDeCarga("el CSV no tiene cabecera", linea);

  const vacia = nombres.indexOf("");
  if (vacia >= 0) {
    fallaDeCarga(
      `la columna ${vacia + 1} de la cabecera no tiene nombre`,
      linea,
    );
  }

  const esperadas = tabla.info.columns.map((c) => c.name);
  const porMinusculas = new Map<string, string[]>();
  for (const nombre of esperadas) {
    const clave = nombre.toLowerCase();
    porMinusculas.set(clave, [...(porMinusculas.get(clave) ?? []), nombre]);
  }

  const vistos = new Map<string, number>();
  const repetidas: string[] = [];
  const sobran: string[] = [];

  nombres.forEach((crudo, posicion) => {
    let nombre = crudo;
    if (!esperadas.includes(nombre)) {
      const candidatas = porMinusculas.get(nombre.toLowerCase()) ?? [];
      if (candidatas.length !== 1) {
        sobran.push(nombre);
        return;
      }
      nombre = candidatas[0];
    }
    if (vistos.has(nombre)) repetidas.push(nombre);
    else vistos.set(nombre, posicion);
  });

  const faltan = esperadas.filter((n) => !vistos.has(n));
  const problemas: string[] = [];
  if (faltan.length > 0) problemas.push(`faltan ${lista(faltan)}`);
  if (sobran.length > 0) problemas.push(`sobran ${lista(sobran)}`);
  if (repetidas.length > 0) problemas.push(`se repite ${lista(repetidas)}`);

  if (problemas.length > 0) {
    fallaDeCarga(
      `la cabecera no calza con la tabla '${tabla.info.name}': ` +
        problemas.join("; ") +
        `. La tabla tiene ${lista(esperadas)}`,
      linea,
    );
  }

  return esperadas.map((nombre) => vistos.get(nombre)!);
}

/**
 * Dias desde 1970-01-01, que es como el core representa una DATE. NaN si la
 * fecha no existe.
 *
 * `Date.UTC` no valida: el mes 13 se lo lleva al ano siguiente y el 30 de
 * febrero al 2 de marzo, sin avisar. `date.fromisoformat` de Python lanza en
 * los dos casos, asi que hay que comprobar que la fecha sobrevivio igual a
 * como entro.
 */
function diasDesdeEpoca(texto: string): number {
  const [ano, mes, dia] = texto.split("-").map(Number);
  // El ano 0 no existe para `date`, cuyo MINYEAR es 1.
  if (ano < 1) return NaN;

  const fecha = new Date(Date.UTC(ano, mes - 1, dia));
  // `Date.UTC` manda los anos de 0 a 99 a 1900-1999; deshacerlo evita
  // rechazar una fecha antigua que Python si acepta.
  if (ano < 100) fecha.setUTCFullYear(ano);

  const existe =
    fecha.getUTCFullYear() === ano &&
    fecha.getUTCMonth() === mes - 1 &&
    fecha.getUTCDate() === dia;
  return existe ? Math.round(fecha.getTime() / 86_400_000) : NaN;
}

/**
 * Convierte un campo al tipo declarado.
 *
 * El texto de un VARCHAR se guarda tal cual, espacios incluidos. En los demas
 * se ignoran los de los bordes. No hay NULL en el motor, asi que un campo
 * vacio solo vale en un VARCHAR. Con `comaDecimal` —un CSV separado por punto
 * y coma— un DOUBLE puede venir como `15,5`.
 */
function convertirCampo(
  texto: string,
  columna: ColumnInfo,
  comaDecimal: boolean,
): CellValue {
  const falla = (mensaje: string): never => {
    throw new Error(`columna ${columna.name}: ${mensaje}`);
  };

  if (columna.type === "VARCHAR") {
    if (texto.includes("\0")) falla("VARCHAR no admite bytes nulos");
    const bytes = new TextEncoder().encode(texto).length;
    if (columna.size !== null && bytes > columna.size) {
      falla(`el texto ocupa ${bytes} bytes y supera VARCHAR(${columna.size})`);
    }
    return texto;
  }

  let valor = texto.trim();
  if (valor === "") {
    falla(`esta vacia y ${columna.type} no admite vacios`);
  }

  if (columna.type === "INT") {
    if (!ENTERO.test(valor)) falla(`'${valor}' no es un INT`);
    const numero = Number(valor.replace(/_/g, ""));
    if (numero < INT32_MIN || numero > INT32_MAX) {
      falla(`${numero} esta fuera del rango INT de 32 bits`);
    }
    return numero;
  }

  if (columna.type === "DOUBLE") {
    if (comaDecimal && valor.includes(",") && !valor.includes(".")) {
      valor = valor.replace(",", ".");
    }
    // `float()` convierte "inf" y "nan", y es el motor el que los rechaza
    // despues por no ser finitos: son dos mensajes distintos.
    if (NO_FINITO.test(valor)) falla("DOUBLE requiere un valor finito");
    if (!DECIMAL.test(valor)) falla(`'${valor}' no es un DOUBLE`);
    return Number(valor.replace(/_/g, ""));
  }

  if (columna.type === "BOOL") {
    const minusculas = valor.toLowerCase();
    if (VERDADERO.has(minusculas)) return true;
    if (FALSO.has(minusculas)) return false;
    falla(`'${valor}' no es un BOOL (true/false o 1/0)`);
  }

  // DATE
  if (!FECHA.test(valor)) falla(`'${valor}' no es una fecha AAAA-MM-DD`);
  const dias = diasDesdeEpoca(valor);
  if (Number.isNaN(dias)) falla(`'${valor}' no es una fecha valida`);
  return dias;
}

/**
 * Convierte una fila al orden y los tipos del esquema.
 *
 * `ancho` es cuantas columnas tiene la cabecera contando las vacias del
 * final: una fila con mas o menos campos es un error aunque las que sobran no
 * se usen, porque casi siempre significa una coma de mas dentro de un texto
 * sin comillas.
 */
function convertirFila(
  campos: string[],
  posiciones: number[],
  columnas: ColumnInfo[],
  ancho: number,
  comaDecimal: boolean,
): CellValue[] {
  if (campos.length !== ancho) {
    throw new Error(
      `tiene ${campos.length} campos y la cabecera tiene ${ancho}`,
    );
  }
  for (let posicion = posiciones.length; posicion < ancho; posicion++) {
    if (campos[posicion].trim() !== "") {
      throw new Error(
        `el campo ${posicion + 1} trae '${campos[posicion]}' y su columna de ` +
          "la cabecera no tiene nombre",
      );
    }
  }
  return columnas.map((columna, indice) =>
    convertirCampo(campos[posiciones[indice]], columna, comaDecimal),
  );
}

/** Devuelve el hilo al navegador para que pinte y atienda los clics. */
function cederElHilo(): Promise<void> {
  return new Promise((listo) => setTimeout(listo, 0));
}

/**
 * Carga el archivo en la tabla y devuelve el informe.
 *
 * La cabecera se empareja con el esquema ANTES de insertar nada, asi que un
 * CSV de otra tabla se rechaza entero (criterio 4). Despues cada fila es su
 * propia unidad: las que no se pueden convertir se cuentan y se reportan con
 * su linea, y las demas quedan.
 */
export async function mockLoadCsv(
  nombre: string,
  archivo: File,
): Promise<LoadResult> {
  // El motor toma su propio lock exclusivo durante todo el archivo, asi que
  // no cabe dentro de una transaccion abierta.
  if (hayTransaccion()) {
    fallaDeCarga(
      "la carga masiva no se puede ejecutar dentro de una transaccion: " +
        "falta un END TRANSACTION",
    );
  }

  const tabla = buscarTabla(nombre);
  if (!tabla) fallaDeCarga(`la tabla '${nombre}' no existe`);

  const { texto, encoding } = decodificar(await archivo.arrayBuffer());

  const cabeceraCruda = primeraLineaConTexto(texto);
  if (cabeceraCruda === "") {
    fallaDeCarga("el archivo esta vacio: falta la cabecera");
  }

  const separador = elegirSeparador(cabeceraCruda);
  const filas = partirFilas(texto, separador);
  if (filas.length === 0) {
    fallaDeCarga("el archivo esta vacio: falta la cabecera");
  }

  const cabecera = filas[0];
  const posiciones = emparejarCabecera(cabecera.campos, tabla!, cabecera.linea);
  const ancho = cabecera.campos.length;
  const columnas = tabla!.info.columns;
  const claveIndice = columnas.findIndex((c) => c.is_primary_key);

  let inserted = 0;
  let failed = 0;
  const errors: LoadRowError[] = [];
  const anotarFallo = (linea: number, error: string) => {
    failed++;
    if (errors.length < MAXIMO_ERRORES) errors.push({ line: linea, error });
  };

  // Las claves ya presentes, en un conjunto: comprobarlas recorriendo la
  // tabla por cada fila convierte una carga de 100 000 filas en cuadratica.
  const claves =
    claveIndice >= 0
      ? new Set(tabla!.filas.map((f) => String(f[claveIndice])))
      : null;

  for (let i = 1; i < filas.length; i++) {
    if (i % FILAS_POR_TANDA === 0) await cederElHilo();

    const { campos, linea } = filas[i];
    let fila: CellValue[];
    try {
      fila = convertirFila(
        campos,
        posiciones,
        columnas,
        ancho,
        separador === ";",
      );
    } catch (fallo) {
      anotarFallo(linea, fallo instanceof Error ? fallo.message : String(fallo));
      continue;
    }

    if (claves) {
      const clave = String(fila[claveIndice]);
      if (claves.has(clave)) {
        anotarFallo(
          linea,
          `no se pudo insertar: la clave primaria ya existe en ${tabla!.info.name}`,
        );
        continue;
      }
      claves.add(clave);
    }

    tabla!.filas.push(fila);
    inserted++;
  }

  tabla!.info.record_count = tabla!.filas.length;

  return {
    table: tabla!.info.name,
    encoding,
    inserted,
    failed,
    errors,
    errors_truncated: failed > errors.length,
  };
}
