/**
 * El catalogo falso y sus filas.
 *
 * Los generadores son deterministas: la misma consulta devuelve siempre lo
 * mismo, que es lo que permite comparar dos ejecuciones durante una demo.
 */

import type { CellValue, DataType, TableInfo } from "@/api/types";

export interface TablaFalsa {
  info: TableInfo;
  tipos: DataType[];
  filas: CellValue[][];
}

const PILA = [
  "ana", "bruno", "carla", "diego", "elena", "fabio", "gina", "hugo",
  "irene", "julio", "karla", "luis", "marta", "nestor", "olga", "pablo",
  "rosa", "sergio", "tania", "victor",
];

const APELLIDOS = [
  "torres", "diaz", "ruiz", "salas", "vega", "ramos", "soto", "lima",
  "paz", "cano", "mora", "bravo", "quispe", "mamani", "flores", "rojas",
  "chavez", "guzman", "ibarra", "ponce", "castro", "medina", "silva",
  "vargas", "reyes",
];

/**
 * Nombre, apellido paterno y materno: 20 x 25 x 25 = 12 500 combinaciones.
 *
 * Los tres indices salen de descomponer el numero en base mixta, asi que dos
 * filas distintas nunca coinciden mientras haya menos de 12 500. Las 10 000 de
 * `alumnos` entran con holgura y ningun nombre se repite.
 *
 * El mas largo mide 20 caracteres, dentro del VARCHAR(32) del esquema.
 */
function nombreDe(indice: number): string {
  const pila = PILA[indice % PILA.length];
  const paterno = APELLIDOS[Math.floor(indice / PILA.length) % APELLIDOS.length];
  // El desfase evita que las primeras filas salgan con el paterno y el
  // materno iguales ("ana torres torres"); que coincidan de vez en cuando mas
  // adelante es normal y ocurre en la vida real.
  const materno =
    APELLIDOS[
      (Math.floor(indice / (PILA.length * APELLIDOS.length)) + 7) %
        APELLIDOS.length
    ];
  return `${pila} ${paterno} ${materno}`;
}

const CURSOS = [
  "Base de Datos 2",
  "Algoritmos",
  "Sistemas Operativos",
  "Redes",
  "Compiladores",
  "Grafica",
];

const DEPARTAMENTOS = ["Computacion", "Matematica", "Fisica", "Industrial"];
const CICLOS = ["2024-1", "2024-2", "2025-1", "2025-2", "2026-1", "2026-2"];

/** Días desde 1970-01-01, que es como el core representa una DATE. */
const DIA_BASE = 18_000;

const alumnos: TablaFalsa = {
  info: {
    name: "alumnos",
    storage: "heap",
    record_count: 10_000,
    columns: [
      { name: "codigo", type: "INT", size: null, is_primary_key: true },
      { name: "nombre", type: "VARCHAR", size: 32, is_primary_key: false },
      { name: "promedio", type: "DOUBLE", size: null, is_primary_key: false },
    ],
    indexes: [
      {
        name: "por_promedio",
        column: "promedio",
        structure: "bplus_unclustered",
        supports_range: true,
      },
    ],
  },
  tipos: ["INT", "VARCHAR", "DOUBLE"],
  filas: Array.from({ length: 10_000 }, (_, i) => [
    1000 + i,
    nombreDe(i),
    Number((10 + ((i * 7) % 101) / 10).toFixed(1)),
  ]),
};

const cursos: TablaFalsa = {
  info: {
    name: "cursos",
    storage: "bplus_clustered",
    record_count: 120,
    columns: [
      { name: "id", type: "INT", size: null, is_primary_key: true },
      { name: "nombre", type: "VARCHAR", size: 40, is_primary_key: false },
      { name: "creditos", type: "INT", size: null, is_primary_key: false },
    ],
    indexes: [],
  },
  tipos: ["INT", "VARCHAR", "INT"],
  filas: Array.from({ length: 120 }, (_, i) => [
    1 + i,
    `${CURSOS[i % CURSOS.length]} ${Math.floor(i / CURSOS.length) + 1}`,
    2 + (i % 4),
  ]),
};

const matriculas: TablaFalsa = {
  info: {
    name: "matriculas",
    storage: "sequential",
    record_count: 4_300,
    columns: [
      { name: "id", type: "INT", size: null, is_primary_key: true },
      { name: "codigo_alumno", type: "INT", size: null, is_primary_key: false },
      { name: "ciclo", type: "VARCHAR", size: 8, is_primary_key: false },
      { name: "activa", type: "BOOL", size: null, is_primary_key: false },
    ],
    indexes: [
      {
        name: "por_alumno",
        column: "codigo_alumno",
        structure: "extendible_hash",
        supports_range: false,
      },
    ],
  },
  tipos: ["INT", "INT", "VARCHAR", "BOOL"],
  filas: Array.from({ length: 4_300 }, (_, i) => [
    1 + i,
    1000 + (i % 10_000),
    CICLOS[i % CICLOS.length],
    i % 3 !== 0,
  ]),
};

const profesores: TablaFalsa = {
  info: {
    name: "profesores",
    storage: "heap",
    record_count: 85,
    columns: [
      { name: "codigo", type: "INT", size: null, is_primary_key: true },
      { name: "nombre", type: "VARCHAR", size: 48, is_primary_key: false },
      {
        name: "departamento",
        type: "VARCHAR",
        size: 24,
        is_primary_key: false,
      },
      { name: "ingreso", type: "DATE", size: null, is_primary_key: false },
      { name: "activo", type: "BOOL", size: null, is_primary_key: false },
    ],
    indexes: [
      {
        name: "por_departamento",
        column: "departamento",
        structure: "extendible_hash",
        supports_range: false,
      },
      {
        name: "por_ingreso",
        column: "ingreso",
        structure: "bplus_unclustered",
        supports_range: true,
      },
    ],
  },
  tipos: ["INT", "VARCHAR", "VARCHAR", "DATE", "BOOL"],
  filas: Array.from({ length: 85 }, (_, i) => [
    500 + i,
    // Desplazado para que un profesor no comparta nombre con el alumno i.
    nombreDe(i * 13 + 7),
    DEPARTAMENTOS[i % DEPARTAMENTOS.length],
    DIA_BASE + i * 37,
    i % 5 !== 0,
  ]),
};

export const TABLAS: TablaFalsa[] = [alumnos, cursos, matriculas, profesores];

export const MOCK_TABLES: TableInfo[] = TABLAS.map((t) => t.info);

export function buscarTabla(nombre: string): TablaFalsa | undefined {
  return TABLAS.find((t) => t.info.name === nombre.toLowerCase());
}
