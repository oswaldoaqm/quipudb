/**
 * `CREATE TABLE` sobre el catalogo falso.
 *
 * La tabla nace vacia y vive mientras dure la sesion del navegador: no hay
 * disco detras. Alcanza para lo que el issue #111 tiene que demostrar —que el
 * Panel de Archivos refleja la tabla nueva sin recargar la pagina—, y el motor
 * real ya prueba la creacion de verdad con sus propias pruebas en C++.
 */

import { MotorError } from "@/api/errors";
import { agregarTabla, buscarTabla, type TablaFalsa } from "@/api/mock/datos";
import { ubicarEn } from "@/api/mock/ubicacion";
import type { ColumnInfo, DataType, TableStructure } from "@/api/types";

const CREATE_TABLE =
  /^\s*CREATE\s+TABLE\s+([a-zA-Z_]\w*)\s*\(([\s\S]*)\)\s*(?:USING\s+([a-zA-Z_]\w*))?\s*;?\s*$/i;

const COLUMNA =
  /^([a-zA-Z_]\w*)\s+(INT|VARCHAR|DOUBLE|BOOL|DATE)(?:\s*\(\s*(\d+)\s*\))?(?:\s+PRIMARY\s+KEY)?$/i;

const ES_CLAVE = /\bPRIMARY\s+KEY\b/i;

const ORGANIZACIONES: Record<string, TableStructure> = {
  heap: "heap",
  sequential: "sequential",
  bplus_clustered: "bplus_clustered",
};

export function esCreateTable(sql: string): boolean {
  return CREATE_TABLE.test(sql);
}

/**
 * Crea la tabla y devuelve su nombre.
 *
 * Los mensajes de error son los del motor: `QueryProcessor` para la tabla
 * repetida y `semantic.py` para los defectos del esquema.
 */
export function ejecutarCreateTable(sql: string): string {
  const partes = CREATE_TABLE.exec(sql);
  if (!partes) {
    throw new MotorError({
      error: "se esperaba TABLE despues de CREATE",
      kind: "parse",
      ...ubicarEn(sql, 0, 6),
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
      error: `${clave} no es una organizacion de tabla valida`,
      kind: "semantic",
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
