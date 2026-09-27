/**
 * `JOIN` de dos tablas y resolucion de nombres calificados.
 *
 * El motor admite como mucho un JOIN por consulta
 * (`Parser._from_source()`), con un ON que es una sola igualdad entre dos
 * columnas. Aqui se reproduce eso y nada mas: un segundo JOIN es un error de
 * sintaxis, igual que alla.
 *
 * El resultado se materializa como una tabla sintetica con el esquema de
 * salida del join, y a partir de ahi el WHERE, el ORDER BY y la proyeccion
 * los resuelve `consulta.ts` sin enterarse de que hubo un join. Lo que el
 * join si decide por su cuenta es su nodo del plan, porque la estrategia y la
 * sonda no se deducen de las filas.
 */

import { MotorError } from "@/api/errors";
import { REFERENCIA, type ConsultaLeida } from "@/api/mock/consulta";
import { buscarTabla, type TablaFalsa } from "@/api/mock/datos";
import { ubicarEn } from "@/api/mock/ubicacion";
import type { CellValue, DataType, IndexInfo, Step } from "@/api/types";

const JOIN = new RegExp(
  `\\bFROM\\s+([a-zA-Z_]\\w*)\\s+JOIN\\s+([a-zA-Z_]\\w*)\\s+ON\\s+(${REFERENCIA})\\s*=\\s*(${REFERENCIA})`,
  "i",
);
const HAY_JOIN = /\bJOIN\b/i;
const CON_ON = /\bJOIN\s+[a-zA-Z_]\w*\s+ON\b/i;

/** Una columna del alcance: de que tabla viene y en que posicion de la salida. */
interface Visible {
  tabla: string;
  columna: string;
  posicion: number;
}

/**
 * Lo que la consulta puede nombrar.
 *
 * `visible` guarda el nombre ORIGINAL de cada columna, antes de que la salida
 * prefije las que colisionan, para que `alumnos.codigo` se resuelva sin
 * depender de como quedo escrita la cabecera (igual que `_Scope` en
 * `engine/parser/semantic.py`).
 */
export interface Alcance {
  nombre: string;
  visible: Visible[];
  /** Nombres tal como salen en la cabecera, ya prefijados si hizo falta. */
  salida: string[];
}

function fallar(sql: string, termino: string, mensaje: string): never {
  const posicion = sql.toLowerCase().indexOf(termino.toLowerCase());
  throw new MotorError({
    error: mensaje,
    kind: "semantic",
    ...ubicarEn(sql, posicion < 0 ? 0 : posicion, termino.length),
  });
}

/**
 * Nombres de la cabecera: solo se prefijan los que aparecen en los dos lados.
 *
 * Es la regla de `ExternalJoin::output_schema()`, copiada en
 * `join_output_schema`. Prefijarlos todos ensuciaria el caso comun.
 */
function nombresDeSalida(izquierda: TablaFalsa, derecha: TablaFalsa): string[] {
  const aIzquierda = izquierda.info.columns.map((c) => c.name);
  const aDerecha = derecha.info.columns.map((c) => c.name);
  const colisionan = new Set(aIzquierda.filter((n) => aDerecha.includes(n)));

  return [
    ...aIzquierda.map((n) =>
      colisionan.has(n) ? `${izquierda.info.name}.${n}` : n,
    ),
    ...aDerecha.map((n) => (colisionan.has(n) ? `${derecha.info.name}.${n}` : n)),
  ];
}

export function alcanceDeTabla(tabla: TablaFalsa): Alcance {
  return {
    nombre: tabla.info.name,
    visible: tabla.info.columns.map((c, posicion) => ({
      tabla: tabla.info.name,
      columna: c.name,
      posicion,
    })),
    salida: tabla.info.columns.map((c) => c.name),
  };
}

function alcanceDeJoin(izquierda: TablaFalsa, derecha: TablaFalsa): Alcance {
  const desplazamiento = izquierda.info.columns.length;
  return {
    nombre: `${izquierda.info.name}_${derecha.info.name}`,
    visible: [
      ...izquierda.info.columns.map((c, posicion) => ({
        tabla: izquierda.info.name,
        columna: c.name,
        posicion,
      })),
      ...derecha.info.columns.map((c, posicion) => ({
        tabla: derecha.info.name,
        columna: c.name,
        posicion: desplazamiento + posicion,
      })),
    ],
    salida: nombresDeSalida(izquierda, derecha),
  };
}

/**
 * Resuelve `tabla.columna` o `columna` a su posicion en el esquema de salida.
 *
 * Una referencia calificada solo mira la tabla que la califica; una sin
 * calificar mira todo el alcance y es un error si aparece en mas de un lado
 * (`_resolve_column` en `semantic.py`).
 */
export function resolver(
  referencia: string,
  alcance: Alcance,
  sql: string,
): number {
  const punto = referencia.indexOf(".");
  const calificador = punto < 0 ? null : referencia.slice(0, punto);
  const nombre = punto < 0 ? referencia : referencia.slice(punto + 1);

  if (calificador !== null) {
    const tablas = [...new Set(alcance.visible.map((v) => v.tabla))].sort();
    if (!tablas.includes(calificador)) {
      fallar(
        sql,
        calificador,
        `el calificador '${calificador}' no corresponde a ninguna tabla de la ` +
          `consulta; hay: ${tablas.join(", ")}`,
      );
    }
  }

  const candidatas = alcance.visible.filter(
    (v) =>
      v.columna === nombre &&
      (calificador === null || v.tabla === calificador),
  );

  if (candidatas.length > 1) {
    const duenas = [...new Set(candidatas.map((v) => v.tabla))].sort();
    fallar(
      sql,
      nombre,
      `la columna '${nombre}' es ambigua: esta en ${duenas.join(", ")}; ` +
        "califica cual quieres",
    );
  }

  if (candidatas.length === 0) {
    const donde =
      calificador !== null
        ? `la tabla '${calificador}'`
        : `la consulta sobre '${alcance.nombre}'`;
    fallar(sql, nombre, `la columna '${nombre}' no existe en ${donde}`);
  }

  return candidatas[0].posicion;
}

/**
 * Reescribe la consulta con los nombres de la cabecera.
 *
 * Despues de esto ninguna referencia lleva calificador y todas existen en la
 * tabla —real o sintetica— que se va a recorrer, que es lo que `ejecutar`
 * espera. Los nombres que no se puedan resolver lanzan aqui, con el mensaje
 * del motor.
 */
export function resolverConsulta(
  consulta: ConsultaLeida,
  alcance: Alcance,
  sql: string,
): ConsultaLeida {
  const nombreDe = (referencia: string) =>
    alcance.salida[resolver(referencia, alcance, sql)];

  return {
    ...consulta,
    columnas: consulta.columnas ? consulta.columnas.map(nombreDe) : null,
    agregados: consulta.agregados.map((a) => {
      if (a.columna === "*") return a;
      const columna = nombreDe(a.columna);
      return { ...a, columna, alias: `${a.funcion}_${columna}` };
    }),
    agrupa: consulta.agrupa ? nombreDe(consulta.agrupa) : null,
    where: consulta.where
      ? { ...consulta.where, columna: nombreDe(consulta.where.columna) }
      : null,
    orden: consulta.orden
      ? { ...consulta.orden, columna: nombreDe(consulta.orden.columna) }
      : null,
  };
}

export interface JoinLeido {
  izquierda: TablaFalsa;
  derecha: TablaFalsa;
  /** Posiciones en el esquema de salida, ya resueltas. */
  posicionIzquierda: number;
  posicionDerecha: number;
  alcance: Alcance;
}

export function hayJoin(sql: string): boolean {
  return HAY_JOIN.test(sql);
}

/**
 * Lee el JOIN y resuelve su ON.
 *
 * El orden de los operandos del ON no importa: `ON b.x = a.y` vale tanto como
 * `ON a.y = b.x`, asi que se prueba el emparejamiento directo y despues el
 * cruzado, como hace `_bind_source`.
 */
export function leerJoin(sql: string): JoinLeido {
  // Dos JOIN es un error del parser, no una consulta que se ejecuta a medias.
  if (sql.match(/\bJOIN\b/gi)!.length > 1) {
    const segundo = sql.toUpperCase().indexOf("JOIN", sql.toUpperCase().indexOf("JOIN") + 4);
    throw new MotorError({
      error: "esta version admite un solo JOIN por consulta",
      kind: "parse",
      ...ubicarEn(sql, segundo, 4),
    });
  }

  if (!CON_ON.test(sql)) {
    throw new MotorError({
      error: "se esperaba ON despues de la tabla del JOIN",
      kind: "parse",
      ...ubicarEn(sql, sql.toUpperCase().indexOf("JOIN"), 4),
    });
  }

  const partes = JOIN.exec(sql);
  if (!partes) {
    throw new MotorError({
      error: "el ON de un JOIN solo admite una igualdad entre dos columnas",
      kind: "parse",
      ...ubicarEn(sql, sql.toUpperCase().indexOf(" ON ") + 1, 2),
    });
  }

  const [, nombreIzquierda, nombreDerecha, refUna, refOtra] = partes;
  const izquierda = buscarTabla(nombreIzquierda);
  const derecha = buscarTabla(nombreDerecha);

  if (!izquierda) fallar(sql, nombreIzquierda, `la tabla '${nombreIzquierda}' no existe`);
  if (!derecha) fallar(sql, nombreDerecha, `la tabla '${nombreDerecha}' no existe`);

  const alcance = alcanceDeJoin(izquierda!, derecha!);
  const corte = izquierda!.info.columns.length;

  // El emparejamiento directo primero; si una de las dos cae del lado
  // equivocado se prueba cruzado antes de dar el ON por malo.
  let una = resolver(refUna, alcance, sql);
  let otra = resolver(refOtra, alcance, sql);
  if (una >= corte && otra < corte) [una, otra] = [otra, una];

  if (una >= corte || otra < corte) {
    fallar(
      sql,
      refUna,
      "el ON de un JOIN compara una columna de cada tabla",
    );
  }

  const tipoIzquierda = izquierda!.tipos[una];
  const tipoDerecha = derecha!.tipos[otra - corte];
  if (tipoIzquierda !== tipoDerecha) {
    fallar(
      sql,
      refUna,
      `no se puede juntar ${alcance.salida[una]} (${tipoIzquierda}) con ` +
        `${alcance.salida[otra]} (${tipoDerecha})`,
    );
  }

  return {
    izquierda: izquierda!,
    derecha: derecha!,
    posicionIzquierda: una,
    posicionDerecha: otra,
    alcance,
  };
}

/** La tabla sintetica con las filas ya juntadas. */
export function materializar(
  join: JoinLeido,
  filasIzquierda: CellValue[][],
  filasDerecha: CellValue[][],
): TablaFalsa {
  const corte = join.izquierda.info.columns.length;
  const posicionDerecha = join.posicionDerecha - corte;

  // Hash join: se indexa el lado derecho y se recorre el izquierdo una vez.
  const cubos = new Map<string, CellValue[][]>();
  for (const fila of filasDerecha) {
    const clave = String(fila[posicionDerecha]);
    const cubo = cubos.get(clave);
    if (cubo) cubo.push(fila);
    else cubos.set(clave, [fila]);
  }

  const filas: CellValue[][] = [];
  for (const fila of filasIzquierda) {
    const pareja = cubos.get(String(fila[join.posicionIzquierda]));
    if (!pareja) continue;
    for (const otra of pareja) filas.push([...fila, ...otra]);
  }

  const columnas = join.alcance.salida.map((name, indice) => {
    const origen =
      indice < corte
        ? join.izquierda.info.columns[indice]
        : join.derecha.info.columns[indice - corte];
    // La clave primaria de un lado no lo es del resultado: un join no tiene.
    return { ...origen, name, is_primary_key: false };
  });

  const tipos: DataType[] = [...join.izquierda.tipos, ...join.derecha.tipos];

  return {
    info: {
      name: join.alcance.nombre,
      storage: "heap",
      record_count: filas.length,
      columns: columnas,
      indexes: [],
    },
    tipos,
    filas,
  };
}

/**
 * El nodo `join` del plan, con la estrategia que pediria el planner.
 *
 * Solo la hoja derecha se puede sondear, y solo si se recorre entera: con un
 * predicado empujado a ese lado, sondear devolveria filas que el WHERE ya
 * descarto, asi que ahi el join va por hash (`_choose_probe`).
 */
export function pasoDeJoin(
  join: JoinLeido,
  izquierda: Step,
  derecha: Step,
  derechaEsScan: boolean,
): Step {
  const columna = join.posicionDerecha - join.izquierda.info.columns.length;
  const clave = join.derecha.info.columns[columna]?.is_primary_key ?? false;
  const porClave =
    derechaEsScan &&
    clave &&
    (join.derecha.info.storage === "sequential" ||
      join.derecha.info.storage === "bplus_clustered");

  const sonda = derechaEsScan && !porClave ? mejorIndice(join) : null;

  const estrategia = porClave || sonda ? "AUTO" : "HASH";
  const detalle =
    estrategia === "HASH"
      ? "estrategia HASH; external hash join"
      : `estrategia AUTO con sonda por ${
          sonda ? `indice ${sonda.name}` : "clave primaria de la tabla derecha"
        }; decide al ejecutar entre indice y hash`;

  const estructura =
    estrategia === "HASH"
      ? "external_hash"
      : (sonda?.structure ?? join.derecha.info.storage);

  const examinados =
    izquierda.stats.records_returned + derecha.stats.records_returned;

  // Un hash join escribe la tabla de hash a disco y la vuelve a leer; con
  // sonda por indice el coste ya esta en las hojas y el nodo no agrega paginas
  // propias. Son estimaciones, como el resto de los contadores del mock: el
  // motor real las reporta desde `OpStats`.
  const paginas =
    estrategia === "HASH"
      ? Math.max(1, Math.ceil(derecha.stats.records_returned / 128))
      : 0;

  return {
    op: "join",
    structure: estructura,
    table: join.alcance.nombre,
    column: join.alcance.salida[join.posicionIzquierda],
    detail: detalle,
    stats: {
      pages_read: paginas,
      pages_written: paginas,
      records_examined: examinados,
      records_returned: 0,
    },
    time_ms: Number((paginas * 0.035).toFixed(2)),
    children: [izquierda, derecha],
  };
}

/** Un hash resuelve la igualdad del ON en un paso; el B+ sirve pero cuesta mas. */
function mejorIndice(join: JoinLeido): IndexInfo | null {
  const columna =
    join.derecha.info.columns[
      join.posicionDerecha - join.izquierda.info.columns.length
    ];
  const candidatos = join.derecha.info.indexes.filter(
    (i) => i.column === columna?.name,
  );
  const rango = { extendible_hash: 0, bplus_unclustered: 1 } as Record<
    string,
    number
  >;
  return (
    [...candidatos].sort(
      (a, b) =>
        (rango[a.structure] ?? 9) - (rango[b.structure] ?? 9) ||
        a.name.localeCompare(b.name),
    )[0] ?? null
  );
}
