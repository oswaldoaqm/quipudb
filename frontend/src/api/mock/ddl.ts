/**
 * `CREATE TABLE` sobre el catalogo falso.
 *
 * La tabla nace vacia y vive mientras dure la sesion del navegador: no hay
 * disco detras. Alcanza para lo que el issue #111 tiene que demostrar —que el
 * Panel de Archivos refleja la tabla nueva sin recargar la pagina—, y el motor
 * real ya prueba la creacion de verdad con sus propias pruebas en C++.
 */

import { MotorError } from "@/api/errors";
import {
  agregarTabla,
  buscarTabla,
  eliminarTabla,
  type TablaFalsa,
} from "@/api/mock/datos";
import { ubicarEn } from "@/api/mock/ubicacion";
import type { ColumnInfo, DataType, IndexInfo, TableStructure } from "@/api/types";

const CREATE_TABLE =
  /^\s*CREATE\s+TABLE\s+([a-zA-Z_]\w*)\s*\(([\s\S]*)\)\s*(?:USING\s+([a-zA-Z_]\w*))?\s*;?\s*$/i;

const COLUMNA =
  /^([a-zA-Z_]\w*)\s+(INT|VARCHAR|DOUBLE|BOOL|DATE)(?:\s*\(\s*(\d+)\s*\))?(?:\s+PRIMARY\s+KEY)?$/i;

const ES_CLAVE = /\bPRIMARY\s+KEY\b/i;

/**
 * Lo unico que `CREATE TABLE ... USING` acepta.
 *
 * `bplus_clustered` NO esta: el parser solo reconoce HEAP y SEQUENTIAL
 * despues de USING (`Parser._create_table`). Existe como organizacion —la
 * tabla `cursos` del catalogo falso la usa— pero no se puede pedir desde SQL.
 */
const ORGANIZACIONES: Record<string, TableStructure> = {
  heap: "heap",
  sequential: "sequential",
};

const DROP_TABLE = /^\s*DROP\s+TABLE\s+([a-zA-Z_]\w*)\s*;?\s*$/i;

const CREATE_INDEX =
  /^\s*CREATE\s+INDEX\s+([a-zA-Z_]\w*)\s+ON\s+([a-zA-Z_]\w*)\s*\(\s*([a-zA-Z_]\w*)\s*\)\s*USING\s+([a-zA-Z_]\w*)\s*;?\s*$/i;

/** Las dos familias de indice secundario, con los alias que acepta el parser. */
const ESTRUCTURAS: Record<string, IndexInfo["structure"]> = {
  bplus: "bplus_unclustered",
  bplus_unclustered: "bplus_unclustered",
  hash: "extendible_hash",
  extendible_hash: "extendible_hash",
};

/** Cualquier CREATE: el que no sea un CREATE INDEX lo atiende el CREATE TABLE. */
export function esCreate(sql: string): boolean {
  return /^\s*CREATE\b/i.test(sql);
}

export function esCreateTable(sql: string): boolean {
  return CREATE_TABLE.test(sql);
}

export function esDropTable(sql: string): boolean {
  return /^\s*DROP\b/i.test(sql);
}

export function esCreateIndex(sql: string): boolean {
  return /^\s*CREATE\s+INDEX\b/i.test(sql);
}

/** Elimina la tabla del catalogo y devuelve su nombre. */
export function ejecutarDropTable(sql: string): string {
  const partes = DROP_TABLE.exec(sql);
  if (!partes) {
    throw new MotorError({
      error: "se esperaba TABLE despues de DROP",
      kind: "parse",
      ...ubicarEn(sql, 0, 4),
    });
  }

  const nombre = partes[1].toLowerCase();
  const posicion = sql.toLowerCase().indexOf(nombre);

  if (!eliminarTabla(nombre)) {
    throw new MotorError({
      error: `la tabla '${partes[1]}' no existe`,
      kind: "semantic",
      ...ubicarEn(sql, posicion, nombre.length),
    });
  }
  return nombre;
}

/** Registra un indice secundario sobre una columna y devuelve su nombre. */
export function ejecutarCreateIndex(sql: string): string {
  const partes = CREATE_INDEX.exec(sql);
  if (!partes) {
    // Se nombra la primera pieza que falta, en el orden en que el parser las
    // pide, para que el mensaje senale la causa y no la consecuencia.
    const falta = !/\bON\b/i.test(sql)
      ? "se esperaba ON despues del nombre del indice"
      : !/\(/.test(sql)
        ? "se esperaba '(' despues del nombre de la tabla"
        : !/\)/.test(sql)
          ? "se esperaba ')' despues de la columna indexada"
          : !/\bUSING\b/i.test(sql)
            ? "se esperaba USING despues de la columna indexada"
            : "se esperaba BPLUS, BPLUS_UNCLUSTERED, HASH o EXTENDIBLE_HASH " +
              "despues de USING";
    throw new MotorError({
      error: falta,
      kind: "parse",
      ...ubicarEn(sql, 0, sql.trim().length),
    });
  }

  const [, nombre, nombreTabla, columna, estructuraTexto] = partes;
  const tabla = buscarTabla(nombreTabla);
  const falla = (mensaje: string, termino: string): never => {
    throw new MotorError({
      error: mensaje,
      kind: "semantic",
      ...ubicarEn(sql, sql.toLowerCase().indexOf(termino.toLowerCase()), termino.length),
    });
  };

  if (!tabla) falla(`la tabla '${nombreTabla}' no existe`, nombreTabla);

  if (!tabla!.info.columns.some((c) => c.name === columna.toLowerCase())) {
    falla(
      `la columna '${columna}' no existe en la tabla '${tabla!.info.name}'`,
      columna,
    );
  }

  if (tabla!.info.indexes.some((i) => i.name === nombre.toLowerCase())) {
    falla(`el indice ${nombre.toLowerCase()} ya existe`, nombre);
  }

  // Un indice no agrupado guarda RIDs, y solo el heap file garantiza que un
  // RID siga apuntando al mismo registro (arquitectura, seccion del B+ no
  // agrupado).
  if (tabla!.info.storage !== "heap") {
    falla(
      `un indice secundario solo se puede montar sobre un heap file, y ${tabla!.info.name} es ${tabla!.info.storage}`,
      nombreTabla,
    );
  }

  const estructura = ESTRUCTURAS[estructuraTexto.toLowerCase()];
  if (!estructura) {
    throw new MotorError({
      error:
        "se esperaba BPLUS, BPLUS_UNCLUSTERED, HASH o EXTENDIBLE_HASH " +
        "despues de USING",
      kind: "parse",
      ...ubicarEn(
        sql,
        sql.toLowerCase().lastIndexOf(estructuraTexto.toLowerCase()),
        estructuraTexto.length,
      ),
    });
  }

  tabla!.info.indexes.push({
    name: nombre.toLowerCase(),
    column: columna.toLowerCase(),
    structure: estructura!,
    supports_range: estructura === "bplus_unclustered",
  });

  return nombre.toLowerCase();
}

/**
 * Crea la tabla y devuelve su nombre.
 *
 * Los mensajes de error son los del motor: `QueryProcessor` para la tabla
 * repetida y `semantic.py` para los defectos del esquema.
 */
export function ejecutarCreateTable(sql: string): string {
  // Un CREATE que no nombra ni TABLE ni INDEX muere en el parser antes de
  // mirar nada mas; sin esto caeria en "0 filas afectadas", que no dice nada.
  if (!/^\s*CREATE\s+TABLE\b/i.test(sql)) {
    const resto = sql.replace(/^\s*CREATE\s*/i, "");
    const palabra = resto.split(/\s+/)[0] ?? "";
    throw new MotorError({
      error: "se esperaba TABLE o INDEX despues de CREATE",
      kind: "parse",
      ...ubicarEn(sql, sql.length - resto.length, palabra.length || 1),
    });
  }

  const partes = CREATE_TABLE.exec(sql);
  if (!partes) {
    throw new MotorError({
      error: "se esperaba '(' despues del nombre de la tabla",
      kind: "parse",
      ...ubicarEn(sql, 0, sql.trim().length),
    });
  }

  const [, nombre, listaColumnas, organizacion] = partes;
  const posicionNombre = sql.toLowerCase().indexOf(nombre.toLowerCase());

  if (buscarTabla(nombre)) {
    throw new MotorError({
      error: `la tabla ${nombre.toLowerCase()} ya existe`,
      kind: "semantic",
      ...ubicarEn(sql, posicionNombre, nombre.length),
    });
  }

  const declaraciones = listaColumnas
    .split(",")
    .map((parte) => parte.trim())
    .filter(Boolean);

  if (declaraciones.length === 0) {
    throw new MotorError({
      error: "la tabla debe declarar al menos una columna",
      kind: "semantic",
      ...ubicarEn(sql, posicionNombre, nombre.length),
    });
  }

  const columns: ColumnInfo[] = [];
  const tipos: DataType[] = [];

  for (const declaracion of declaraciones) {
    const columna = COLUMNA.exec(declaracion);
    if (!columna) {
      throw new MotorError({
        error: `se esperaba el nombre de una columna y su tipo, y se encontro '${declaracion}'`,
        kind: "parse",
        ...ubicarEn(sql, sql.indexOf(declaracion), declaracion.length),
      });
    }

    const [, nombreColumna, tipoTexto, tamano] = columna;
    const tipo = tipoTexto.toUpperCase() as DataType;

    if (columns.some((c) => c.name === nombreColumna.toLowerCase())) {
      throw new MotorError({
        error: `columna repetida en ${nombre.toLowerCase()}: ${nombreColumna.toLowerCase()}`,
        kind: "semantic",
        ...ubicarEn(sql, sql.indexOf(declaracion), nombreColumna.length),
      });
    }

    columns.push({
      name: nombreColumna.toLowerCase(),
      type: tipo,
      // Solo VARCHAR declara tamano; el resto lo tiene fijo por tipo.
      size: tipo === "VARCHAR" ? Number(tamano ?? 0) || null : null,
      is_primary_key: ES_CLAVE.test(declaracion),
    });
    tipos.push(tipo);
  }

  const clave = organizacion?.toLowerCase() ?? "heap";
  const storage = ORGANIZACIONES[clave];
  if (!storage) {
    throw new MotorError({
      error: "se esperaba HEAP o SEQUENTIAL despues de USING",
      kind: "parse",
      ...ubicarEn(sql, sql.toLowerCase().lastIndexOf(clave), clave.length),
    });
  }

  const tabla: TablaFalsa = {
    info: {
      name: nombre.toLowerCase(),
      storage,
      record_count: 0,
      columns,
      indexes: [],
    },
    tipos,
    filas: [],
  };

  agregarTabla(tabla);
  return tabla.info.name;
}
