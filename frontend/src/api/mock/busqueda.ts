/**
 * SELECT con MATCH (Parte 3) o SIMILAR_TO (Parte 4) sobre los datos falsos.
 *
 * Va aparte del interprete de `consulta.ts` porque estas consultas no se
 * dejan leer con una expresion regular por clausula: el puntaje puede ir en
 * cualquier posicion de la proyeccion, con o sin alias, y el ORDER BY puede
 * nombrarlo por ese alias. Aqui se tokeniza la sentencia y se lee con la
 * misma gramatica de `Parser._select` en `engine/parser/parser.py`.
 *
 * Los mensajes de error son literales del parser, de `semantic.py` y de
 * `QueryProcessor`, y el plan reproduce el que arma el motor al ejecutar
 * (`explain.py`): `text_search` o `knn_search`, el `fetch` por RID, el `sort`
 * en memoria cuando se ordena por el puntaje, `project` y `limit`.
 */

import { MotorError } from "@/api/errors";
import {
  armarPlan,
  comparar,
  paginas,
  paso,
  stats,
} from "@/api/mock/consulta";
import { TABLAS, buscarTabla, type Metrica, type TablaFalsa } from "@/api/mock/datos";
import { ARCHIVOS_DE_CONSULTA } from "@/api/mock/datos-multimodal";
import { alcanceDeTabla, resolver } from "@/api/mock/juntar";
import { buscarTexto, type Ranking } from "@/api/mock/texto";
import { ubicarEn } from "@/api/mock/ubicacion";
import { MENOR_ES_MEJOR, vecinos } from "@/api/mock/vectores";
import type {
  CellValue,
  DataType,
  ErrorKind,
  QueryResult,
  Step,
} from "@/api/types";

/** Los parametros de busqueda con que arranca la API (`QueryProcessor`). */
const EF_SEARCH = 64;
const NPROBE = 1;
const NLIST = 16;

// --- Tokens -----------------------------------------------------------------

interface Token {
  tipo: "palabra" | "numero" | "cadena" | "simbolo" | "fin";
  texto: string;
  inicio: number;
}

const LEXICO =
  /\s+|--[^\n]*|\/\*[\s\S]*?\*\/|('(?:[^']|'')*')|(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)|([A-Za-z_][A-Za-z0-9_]*)|(<=|>=|[=<>(),.*;])/y;

function tokenizar(sql: string): Token[] {
  const tokens: Token[] = [];
  LEXICO.lastIndex = 0;
  while (LEXICO.lastIndex < sql.length) {
    const inicio = LEXICO.lastIndex;
    const m = LEXICO.exec(sql);
    if (!m) {
      throw new MotorError({
        error: `caracter inesperado '${sql[inicio]}'`,
        kind: "lex",
        ...ubicarEn(sql, inicio, 1),
      });
    }
    const [texto, cadena, numero, palabra, simbolo] = m;
    if (cadena) tokens.push({ tipo: "cadena", texto, inicio });
    else if (numero) tokens.push({ tipo: "numero", texto, inicio });
    else if (palabra) tokens.push({ tipo: "palabra", texto, inicio });
    else if (simbolo) tokens.push({ tipo: "simbolo", texto, inicio });
  }
  tokens.push({ tipo: "fin", texto: "", inicio: sql.length });
  return tokens;
}

/** Valor de un literal de cadena, con `''` como comilla escapada. */
function valorDeCadena(token: Token): string {
  return token.texto.slice(1, -1).replace(/''/g, "'");
}

// --- Lo que se lee ----------------------------------------------------------

interface Ref {
  /** `columna` o `tabla.columna`, en minusculas. */
  texto: string;
  inicio: number;
  fin: number;
}

type ClasePuntaje = "SCORE" | "SIMILARITY_SCORE";

type Proyeccion =
  | { tipo: "todo"; inicio: number }
  | { tipo: "columna"; ref: Ref }
  | { tipo: "puntaje"; clase: ClasePuntaje; alias: string | null; inicio: number; fin: number }
  | { tipo: "agregado"; inicio: number; fin: number };

interface Match {
  tipo: "match";
  columna: Ref;
  consulta: string;
  ranking: Ranking;
  explicito: boolean;
  inicio: number;
  fin: number;
}

interface Similar {
  tipo: "similar";
  columna: Ref | null;
  archivo: string | null;
  vector: number[] | null;
  consultaInicio: number;
  consultaFin: number;
  k: number;
  usando: "hnsw" | "ivf";
  metrica: Metrica;
  /** Donde senalar un error de metrica: el valor de METRIC, o HNSW/IVF si se omitio. */
  metricaInicio: number;
  metricaFin: number;
  inicio: number;
  fin: number;
}

type Donde = Match | Similar | { tipo: "otra"; inicio: number; fin: number };

type Orden =
  | { tipo: "puntaje"; clase: ClasePuntaje; descendente: boolean; inicio: number; fin: number }
  | { tipo: "columna"; ref: Ref; descendente: boolean; inicio: number; fin: number };

interface Lectura {
  proyecciones: Proyeccion[];
  tabla: string;
  tablaInicio: number;
  join: number | null;
  donde: Donde | null;
  agrupa: number | null;
  orden: Orden | null;
  limite: number | null;
}

const METRICAS = new Set<Metrica>(["euclidean", "cosine", "dot"]);

class Lector {
  private posicion = 0;
  private readonly tokens: Token[];

  constructor(private readonly sql: string) {
    this.tokens = tokenizar(sql);
  }

  private ver(desfase = 0): Token {
    return this.tokens[Math.min(this.posicion + desfase, this.tokens.length - 1)];
  }

  private anterior(): Token {
    return this.tokens[this.posicion - 1];
  }

  private avanzar(): Token {
    const token = this.ver();
    if (token.tipo !== "fin") this.posicion += 1;
    return token;
  }

  private es(texto: string, desfase = 0): boolean {
    const token = this.ver(desfase);
    return token.tipo !== "cadena" && token.texto.toUpperCase() === texto;
  }

  private acepta(texto: string): boolean {
    if (!this.es(texto)) return false;
    this.avanzar();
    return true;
  }

  error(token: Token, mensaje: string, kind: ErrorKind = "parse"): MotorError {
    return new MotorError({
      error: mensaje,
      kind,
      ...ubicarEn(this.sql, token.inicio, Math.max(1, token.texto.length)),
    });
  }

  private espera(texto: string, mensaje: string): Token {
    if (!this.es(texto)) throw this.error(this.ver(), mensaje);
    return this.avanzar();
  }

  private identificador(mensaje: string): Token {
    const token = this.ver();
    if (token.tipo !== "palabra") throw this.error(token, mensaje);
    return this.avanzar();
  }

  private referencia(mensaje: string): Ref {
    const primero = this.identificador(mensaje);
    let ultimo = primero;
    let texto = primero.texto;
    if (this.acepta(".")) {
      ultimo = this.identificador("se esperaba el nombre de la columna despues de '.'");
      texto = `${primero.texto}.${ultimo.texto}`;
    }
    return {
      texto: texto.toLowerCase(),
      inicio: primero.inicio,
      fin: ultimo.inicio + ultimo.texto.length,
    };
  }

  private fin(token: Token): number {
    return token.inicio + token.texto.length;
  }

  leer(): Lectura {
    this.espera("SELECT", "se esperaba SELECT");
    const proyecciones = this.proyecciones();
    this.espera("FROM", "se esperaba FROM despues de la proyeccion");
    const tabla = this.identificador("se esperaba el nombre de la tabla");

    let join: number | null = null;
    if (this.es("JOIN")) {
      join = this.ver().inicio;
      while (!["WHERE", "GROUP", "ORDER", "LIMIT"].some((p) => this.es(p)) && this.ver().tipo !== "fin" && !this.es(";")) {
        this.avanzar();
      }
    }

    const donde = this.acepta("WHERE") ? this.condicion() : null;

    let agrupa: number | null = null;
    if (this.es("GROUP")) {
      agrupa = this.avanzar().inicio;
      this.espera("BY", "se esperaba BY despues de GROUP");
      this.referencia("se esperaba una columna despues de GROUP BY");
    }

    const orden = this.es("ORDER") ? this.orden() : null;

    let limite: number | null = null;
    if (this.acepta("LIMIT")) {
      const valor = this.ver();
      if (valor.tipo !== "numero" || !/^-?\d+$/.test(valor.texto)) {
        throw this.error(valor, "LIMIT requiere un entero no negativo");
      }
      if (valor.texto.startsWith("-")) throw this.error(valor, "LIMIT no admite valores negativos");
      limite = Number(this.avanzar().texto);
    }

    this.acepta(";");
    const resto = this.ver();
    if (resto.tipo !== "fin") {
      if (resto.texto.toUpperCase() === "AND") {
        throw this.error(resto, "AND solo se admite como separador dentro de BETWEEN", "unsupported");
      }
      throw this.error(resto, `entrada inesperada '${resto.texto}' al final de la sentencia`);
    }

    return {
      proyecciones,
      tabla: tabla.texto.toLowerCase(),
      tablaInicio: tabla.inicio,
      join,
      donde,
      agrupa,
      orden,
      limite,
    };
  }

  private proyecciones(): Proyeccion[] {
    if (this.es("*")) {
      const lista: Proyeccion[] = [{ tipo: "todo", inicio: this.avanzar().inicio }];
      // `*, SCORE() AS relevancia` es la consulta del enunciado (#169).
      while (this.acepta(",")) {
        if (!this.es("SCORE") && !this.es("SIMILARITY_SCORE")) {
          throw this.error(
            this.ver(),
            "'*' no se puede combinar con otras proyecciones, salvo SCORE() o SIMILARITY_SCORE()",
          );
        }
        lista.push(this.puntaje());
      }
      return lista;
    }
    const lista = [this.proyeccion()];
    while (this.acepta(",")) lista.push(this.proyeccion());
    return lista;
  }

  private proyeccion(): Proyeccion {
    if (this.es("SCORE") || this.es("SIMILARITY_SCORE")) return this.puntaje();
    if (["COUNT", "SUM", "MIN", "MAX", "AVG"].some((f) => this.es(f)) && this.es("(", 1)) {
      const inicio = this.avanzar().inicio;
      while (!this.es(")") && this.ver().tipo !== "fin") this.avanzar();
      const cierre = this.espera(")", "se esperaba ')' despues del argumento del agregado");
      return { tipo: "agregado", inicio, fin: this.fin(cierre) };
    }
    return { tipo: "columna", ref: this.referencia("se esperaba una columna o funcion de agregado") };
  }

  private puntaje(): Proyeccion {
    const nombre = this.avanzar();
    const clase = nombre.texto.toUpperCase() as ClasePuntaje;
    this.espera("(", `se esperaba '(' despues de ${clase}`);
    let fin = this.fin(this.espera(")", `${clase}() no recibe argumentos`));
    let alias: string | null = null;
    if (this.acepta("AS")) {
      const token = this.identificador("se esperaba un nombre despues de AS");
      alias = token.texto.toLowerCase();
      fin = this.fin(token);
    }
    return { tipo: "puntaje", clase, alias, inicio: nombre.inicio, fin };
  }

  private condicion(): Donde {
    if (this.es("MATCH")) return this.match();
    if (this.es("SIMILAR_TO")) return this.similar();
    // Otra condicion: no se ejecuta aqui. Solo hace falta saber que no es
    // MATCH ni SIMILAR_TO, para el error de SCORE() sin su busqueda.
    const inicio = this.ver().inicio;
    let fin = inicio;
    while (!["GROUP", "ORDER", "LIMIT"].some((p) => this.es(p)) && this.ver().tipo !== "fin" && !this.es(";")) {
      fin = this.fin(this.avanzar());
    }
    return { tipo: "otra", inicio, fin };
  }

  private match(): Match {
    const inicio = this.avanzar().inicio;
    this.espera("(", "se esperaba '(' despues de MATCH");
    const columna = this.referencia("se esperaba una columna en MATCH");
    this.espera(",", "se esperaba ',' despues de la columna de MATCH");
    const consulta = this.ver();
    if (consulta.tipo !== "cadena") {
      throw this.error(consulta, "MATCH necesita la consulta como texto entre comillas");
    }
    this.avanzar();
    let fin = this.fin(this.espera(")", "se esperaba ')' despues de la consulta de MATCH"));

    let ranking: Ranking = "bm25";
    let explicito = false;
    if (this.acepta("USING")) {
      if (this.acepta("TF_IDF")) ranking = "tf_idf";
      else if (this.acepta("BM25")) ranking = "bm25";
      else throw this.error(this.ver(), "se esperaba TF_IDF o BM25 como ranking de MATCH");
      explicito = true;
      fin = this.fin(this.anterior());
    }
    return { tipo: "match", columna, consulta: valorDeCadena(consulta), ranking, explicito, inicio, fin };
  }

  private similar(): Similar {
    const inicio = this.avanzar().inicio;
    this.espera("(", "se esperaba '(' despues de SIMILAR_TO");

    let columna: Ref | null = null;
    if (this.ver().tipo !== "cadena" && !this.es("VECTOR")) {
      columna = this.referencia("se esperaba la columna VECTOR o el archivo");
      this.espera(",", "se esperaba ',' despues de la columna");
    }

    let archivo: string | null = null;
    let vector: number[] | null = null;
    const consultaInicio = this.ver().inicio;
    if (this.es("VECTOR")) {
      // Un vector calculado fuera del motor (#251): no hay archivo que extraer.
      this.avanzar();
      vector = this.vectorLiteral();
    } else {
      const token = this.ver();
      if (token.tipo !== "cadena") {
        throw this.error(token, "SIMILAR_TO requiere un archivo entre comillas o un VECTOR(...)");
      }
      archivo = valorDeCadena(this.avanzar());
    }
    const consultaFin = this.fin(this.anterior());

    this.espera(",", "se esperaba ',' antes de k");
    if (this.ver().tipo === "palabra") {
      const nombre = this.avanzar();
      if (nombre.texto.toLowerCase() !== "k") {
        throw this.error(nombre, "se esperaba k como parametro de SIMILAR_TO");
      }
      this.espera("=", "se esperaba '=' despues de k");
    }
    const k = this.ver();
    if (k.tipo !== "numero" || !/^-?\d+$/.test(k.texto) || k.texto.startsWith("-")) {
      throw this.error(k, "k requiere un entero no negativo");
    }
    this.avanzar();
    this.espera(")", "se esperaba ')' despues de k");
    this.espera("USING", "SIMILAR_TO requiere USING HNSW o IVF");

    let usando: "hnsw" | "ivf";
    if (this.acepta("HNSW")) usando = "hnsw";
    else if (this.acepta("IVF")) usando = "ivf";
    else throw this.error(this.ver(), "SIMILAR_TO requiere un indice HNSW o IVF");

    let metrica: Metrica = "cosine";
    let senal = this.anterior();
    if (this.acepta("WITH")) {
      this.espera("METRIC", "se esperaba METRIC despues de WITH");
      this.espera("=", "se esperaba '=' despues de METRIC");
      const valor = this.ver();
      const nombre = valor.texto.toLowerCase() as Metrica;
      if (!METRICAS.has(nombre)) throw this.error(valor, "la metrica debe ser euclidean, cosine o dot");
      metrica = nombre;
      senal = this.avanzar();
    }

    return {
      tipo: "similar",
      columna,
      archivo,
      vector,
      consultaInicio,
      consultaFin,
      k: Number(k.texto),
      usando,
      metrica,
      metricaInicio: senal.inicio,
      metricaFin: this.fin(senal),
      inicio,
      fin: this.fin(senal),
    };
  }

  private vectorLiteral(): number[] {
    this.espera("(", "se esperaba '(' despues de VECTOR");
    const componentes: number[] = [];
    for (;;) {
      const token = this.ver();
      if (token.tipo !== "numero") {
        throw this.error(token, `se esperaba un numero en la componente ${componentes.length + 1} de VECTOR`);
      }
      componentes.push(Number(this.avanzar().texto));
      if (!this.acepta(",")) break;
    }
    this.espera(")", "se esperaba ',' o ')' despues de una componente de VECTOR");
    return componentes;
  }

  private orden(): Orden {
    const inicio = this.avanzar().inicio;
    this.espera("BY", "se esperaba BY despues de ORDER");
    const direccion = () => {
      if (this.acepta("DESC")) return true;
      this.acepta("ASC");
      return false;
    };
    if ((this.es("SCORE") || this.es("SIMILARITY_SCORE")) && this.es("(", 1)) {
      const clase = this.avanzar().texto.toUpperCase() as ClasePuntaje;
      this.espera("(", `se esperaba '(' despues de ${clase}`);
      this.espera(")", `${clase}() no recibe argumentos`);
      const descendente = direccion();
      return { tipo: "puntaje", clase, descendente, inicio, fin: this.fin(this.anterior()) };
    }
    if (this.es("DISTANCIA")) {
      throw this.error(
        this.ver(),
        "el simulador no ordena por DISTANCIA una busqueda de texto o de similitud; conecta el motor (VITE_USE_MOCK=false)",
        "unsupported",
      );
    }
    const ref = this.referencia("se esperaba una columna despues de ORDER BY");
    const descendente = direccion();
    return { tipo: "columna", ref, descendente, inicio, fin: this.fin(this.anterior()) };
  }
}

// --- Validacion y ejecucion -------------------------------------------------

/** Lo que va entre comillas es dato: no cuenta como MATCH ni SCORE(). */
function sinCadenas(sql: string): string {
  return sql.replace(/'(?:[^']|'')*'/g, (cita) => " ".repeat(cita.length));
}

/** Si la sentencia es un SELECT que esta parte del simulador debe atender. */
export function esBusqueda(sql: string): boolean {
  const fuera = sinCadenas(sql);
  return (
    /^\s*SELECT\b/i.test(fuera) &&
    /\b(?:MATCH|SIMILAR_TO|SCORE|SIMILARITY_SCORE)\s*\(/i.test(fuera)
  );
}

function falla(
  sql: string,
  inicio: number,
  fin: number,
  mensaje: string,
  kind: ErrorKind = "semantic",
): never {
  throw new MotorError({ error: mensaje, kind, ...ubicarEn(sql, inicio, Math.max(1, fin - inicio)) });
}

/** La fila de un objeto multimedia cuya `ruta` es el archivo, en cualquier tabla. */
function vectorDelArchivo(archivo: string): number[] | undefined {
  const conocido = ARCHIVOS_DE_CONSULTA[archivo];
  if (conocido) return conocido;
  for (const tabla of TABLAS) {
    const ruta = tabla.info.columns.findIndex((c) => c.name === "ruta");
    const rasgos = tabla.info.columns.findIndex((c) => c.type === "VECTOR");
    if (ruta < 0 || rasgos < 0) continue;
    const fila = tabla.filas.find((f) => f[ruta] === archivo);
    if (fila && Array.isArray(fila[rasgos])) return fila[rasgos] as number[];
  }
  return undefined;
}

/**
 * Lo que `VectorSearch.paths` y `extract` hacen antes de buscar: vocabulario
 * configurado, archivo dentro de la raiz multimedia y dimension correcta.
 */
function extraer(sql: string, tabla: TablaFalsa, donde: Similar, dimension: number): number[] {
  const senalar = (mensaje: string): never =>
    falla(sql, donde.consultaInicio, donde.consultaFin, mensaje);
  const archivo = donde.archivo!;

  if (!archivo || archivo.includes("\0")) senalar("SIMILAR_TO requiere una ruta de archivo valida");
  if (archivo.startsWith("/") || /^[a-zA-Z]:/.test(archivo) || archivo.split(/[\\/]/).includes("..")) {
    senalar("la ruta debe estar dentro de QUIPUDB_MEDIA_ROOT");
  }
  if (!tabla.vocabulario) {
    senalar(
      `falta configurar el vocabulario de '${tabla.info.name}' en media_vocabularies ` +
        "o QUIPUDB_MEDIA_VOCABULARIES",
    );
  }
  const vector = vectorDelArchivo(archivo);
  if (!vector) {
    senalar(
      `no se puede abrir la ruta: [Errno 2] No such file or directory: 'datos/multimedia/${archivo}'`,
    );
  }
  if (vector!.length !== dimension) {
    senalar(
      `no se pudo extraer el archivo de consulta: el vocabulario tiene ${vector!.length} ` +
        `palabras y la columna es VECTOR(${dimension})`,
    );
  }
  return vector!;
}

/** `ExternalSort` sobre los registros, como lo describe el plan del motor. */
function pasoDeOrden(tabla: TablaFalsa, columna: string, descendente: boolean, filas: number, hijo: Step): Step {
  const runs = Math.max(1, Math.ceil(paginas(filas) / 8));
  return paso(
    "sort", "external_sort", tabla.info.name, columna,
    `k-way merge, ${runs} runs, ${descendente ? "DESC" : "ASC"}`,
    { pages_read: paginas(filas), pages_written: paginas(filas), records_examined: filas, records_returned: filas },
    [hijo],
  );
}

/** Ejecuta un SELECT con MATCH, SIMILAR_TO, SCORE() o SIMILARITY_SCORE(). */
export function ejecutarBusqueda(sql: string): QueryResult {
  const lectura = new Lector(sql).leer();
  const { proyecciones, donde, orden, limite } = lectura;

  const tabla = buscarTabla(lectura.tabla);
  if (!tabla) {
    falla(sql, lectura.tablaInicio, lectura.tablaInicio + lectura.tabla.length, `la tabla '${lectura.tabla}' no existe`);
  }
  const alcance = alcanceDeTabla(tabla!);
  const nombres = tabla!.info.columns.map((c) => c.name);
  const posicionDe = (ref: Ref) => resolver(ref.texto, alcance, sql);

  // --- semantic.py: bind_select -------------------------------------------
  if (donde?.tipo === "similar" && (lectura.join !== null || lectura.agrupa !== null)) {
    falla(sql, donde.inicio, donde.fin, "SIMILAR_TO requiere una sola tabla sin JOIN ni GROUP BY", "unsupported");
  }

  const puntajes = proyecciones.filter((p) => p.tipo === "puntaje");
  if (puntajes.length > 1) {
    falla(sql, puntajes[1].inicio, puntajes[1].fin, "SCORE()/SIMILARITY_SCORE() solo puede aparecer una vez");
  }
  const llamada = puntajes[0] ?? null;

  const agregado = proyecciones.find((p) => p.tipo === "agregado");
  if (agregado && lectura.agrupa === null) {
    falla(sql, agregado.inicio, agregado.fin, "las funciones de agregado requieren una clausula GROUP BY");
  }

  const columnas = proyecciones.flatMap((p) =>
    p.tipo === "columna" ? [{ ref: p.ref, posicion: posicionDe(p.ref) }] : [],
  );

  // El alias de SCORE() gana sobre una columna con el mismo nombre.
  const nombrePuntaje = llamada ? llamada.alias ?? llamada.clase.toLowerCase() : null;
  let ordenPuntaje: { clase: ClasePuntaje; descendente: boolean; inicio: number; fin: number } | null = null;
  let ordenColumna: { posicion: number; descendente: boolean } | null = null;
  if (orden?.tipo === "puntaje") {
    ordenPuntaje = orden;
  } else if (orden?.tipo === "columna" && llamada && !orden.ref.texto.includes(".") && orden.ref.texto === nombrePuntaje) {
    ordenPuntaje = { ...orden, clase: llamada.clase };
  } else if (orden?.tipo === "columna") {
    ordenColumna = { posicion: posicionDe(orden.ref), descendente: orden.descendente };
  }

  if (donde?.tipo === "match") {
    const posicion = posicionDe(donde.columna);
    const columna = tabla!.info.columns[posicion];
    if (columna.type !== "VARCHAR") {
      falla(sql, donde.columna.inicio, donde.columna.fin, `MATCH requiere una columna VARCHAR; ${columna.name} es ${columna.type}`);
    }
    if (lectura.join !== null) {
      falla(
        sql, donde.inicio, donde.fin,
        "MATCH sobre una consulta con JOIN no esta soportado: el indice de texto " +
          "rankea los documentos de una sola tabla",
        "unsupported",
      );
    }
  }

  let posicionVector = -1;
  if (donde?.tipo === "similar") {
    if (donde.columna) {
      posicionVector = posicionDe(donde.columna);
      if (tabla!.info.columns[posicionVector].type !== "VECTOR") {
        falla(sql, donde.columna.inicio, donde.columna.fin, "SIMILAR_TO requiere una columna VECTOR");
      }
    } else {
      const candidatas = tabla!.info.columns.flatMap((c, i) => (c.type === "VECTOR" ? [i] : []));
      if (candidatas.length !== 1) {
        falla(sql, donde.inicio, donde.fin, "SIMILAR_TO necesita una unica columna VECTOR o una columna explicita");
      }
      posicionVector = candidatas[0];
    }
    const dimension = tabla!.info.columns[posicionVector].size ?? 0;
    if (donde.vector) {
      const nombre = tabla!.info.columns[posicionVector].name;
      if (donde.vector.length !== dimension) {
        falla(
          sql, donde.consultaInicio, donde.consultaFin,
          `columna ${nombre}: el vector tiene ${donde.vector.length} componentes ` +
            `y la columna declara VECTOR(${dimension})`,
        );
      }
    }
  }

  // _validate_score: cada puntaje exige su propia busqueda.
  const clase = llamada?.clase ?? ordenPuntaje?.clase ?? null;
  if (clase) {
    const etiqueta = `${clase}()`;
    const senal = llamada ?? ordenPuntaje!;
    if (ordenPuntaje && ordenPuntaje.clase !== clase) {
      falla(sql, ordenPuntaje.inicio, ordenPuntaje.fin, "no se puede mezclar SCORE() con SIMILARITY_SCORE()");
    }
    const predicado = clase === "SCORE" ? "MATCH" : "SIMILAR_TO";
    if (donde?.tipo !== (clase === "SCORE" ? "match" : "similar")) {
      falla(sql, senal.inicio, senal.fin, `${etiqueta} es el puntaje de ${predicado}: la consulta necesita ${predicado} en el WHERE`);
    }
    if (lectura.agrupa !== null) {
      falla(
        sql, senal.inicio, senal.fin,
        `${etiqueta} con GROUP BY no esta soportado: el puntaje es de cada objeto, no de un grupo`,
        "unsupported",
      );
    }
    if (ordenColumna && orden) {
      falla(
        sql, orden.inicio, orden.fin,
        `${etiqueta} con ORDER BY sobre otra columna no esta soportado: el External ` +
          "Sorting ordena registros de la tabla y el puntaje no viaja con ellos. " +
          `Ordena por el puntaje, o quita ${etiqueta}`,
        "unsupported",
      );
    }
  }

  // Lo que queda fuera de esta parte del simulador se dice, no se inventa.
  if (lectura.join !== null || lectura.agrupa !== null || donde?.tipo !== "match" && donde?.tipo !== "similar") {
    const inicio = lectura.join ?? lectura.agrupa ?? donde?.inicio ?? 0;
    falla(
      sql, inicio, inicio + 4,
      "el simulador no reproduce esta combinacion; conecta el motor (VITE_USE_MOCK=false)",
      "unsupported",
    );
  }

  // --- QueryProcessor y optimizer -------------------------------------------
  const nombreTabla = tabla!.info.name;
  let filasElegidas: { fila: number; puntaje: number }[];
  let acceso: Step;

  if (donde!.tipo === "match") {
    const match = donde as Match;
    const posicion = posicionDe(match.columna);
    const columna = nombres[posicion];
    const indice = tabla!.info.indexes.find(
      (i) => i.structure === "inverted_index" && i.column === columna,
    );
    if (!indice) {
      falla(
        sql, match.columna.inicio, match.columna.fin,
        `la columna '${columna}' no tiene un indice de texto; crealo con ` +
          `CREATE INDEX ... ON ${nombreTabla} (${columna}) USING FULLTEXT`,
      );
    }
    // Sin ORDER BY, o con el puntaje DESC, las filas salen por relevancia y el
    // LIMIT es el top-k del indice.
    const topK = limite !== null && !ordenColumna && !(ordenPuntaje && !ordenPuntaje.descendente);
    const busqueda = buscarTexto(
      tabla!.filas.map((f) => String(f[posicion] ?? "")),
      match.consulta,
      match.ranking,
    );
    filasElegidas = topK ? busqueda.coincidencias.slice(0, limite!) : busqueda.coincidencias;

    const idioma = tabla!.opciones?.[indice!.name]?.idioma ?? "spanish";
    const texto = paso(
      "text_search", "inverted_index", nombreTabla, columna,
      `indice ${indice!.name}; ${sql.slice(match.inicio, match.fin)}; ` +
        `ranking ${match.ranking} (${match.explicito ? "elegido con USING" : "por omision"}); ` +
        `${topK ? `top-${limite}` : "todos los que coinciden"}; idioma ${idioma}`,
      stats(1 + 2 * busqueda.terminos, busqueda.postings, filasElegidas.length),
    );
    texto.ranking = match.ranking;
    acceso = paso(
      "fetch", tabla!.info.storage, nombreTabla, null,
      `lee ${filasElegidas.length} registros por RID`,
      stats(filasElegidas.length, filasElegidas.length, filasElegidas.length),
      [texto],
    );
  } else {
    const similar = donde as Similar;
    const columna = nombres[posicionVector];
    const dimension = tabla!.info.columns[posicionVector].size ?? 0;
    const consulta = similar.vector ?? extraer(sql, tabla!, similar, dimension);

    const candidatos = tabla!.info.indexes
      .filter((i) => i.column === columna && i.structure === similar.usando)
      .sort((a, b) => a.name.localeCompare(b.name));
    let indice = candidatos[0];
    if (similar.usando === "hnsw" && candidatos.length > 0) {
      // La metrica de un HNSW queda fija al construirlo.
      indice = candidatos.find(
        (i) => (tabla!.opciones?.[i.name]?.metrica ?? "cosine") === similar.metrica,
      )!;
      if (!indice) {
        falla(
          sql, similar.metricaInicio, similar.metricaFin,
          "la metrica solicitada no coincide con la del indice HNSW; " +
            "crea un HNSW con esa metrica o usa IVF",
        );
      }
    }

    const total = tabla!.filas.length;
    filasElegidas = vecinos(
      tabla!.filas.map((f) => (Array.isArray(f[posicionVector]) ? (f[posicionVector] as number[]) : null)),
      consulta,
      similar.k,
      similar.metrica,
    );

    const estrategia = indice
      ? `indice ${indice.name}; ${indice.structure === "ivf" ? `nprobe=${NPROBE}` : `ef_search=${EF_SEARCH}`}`
      : `busqueda exacta; sin indice ${similar.usando.toUpperCase()} aplicable`;
    const prefijo = similar.vector ? `consulta VECTOR literal de ${similar.vector.length} componentes; ` : "";
    const examinados = !indice
      ? total
      : indice.structure === "hnsw"
        ? Math.min(total, EF_SEARCH + similar.k)
        : Math.ceil(total / Math.min(NLIST, Math.max(1, total))) * NPROBE;
    const knn = paso(
      "knn_search", indice ? indice.structure : tabla!.info.storage, nombreTabla, columna,
      `${prefijo}${estrategia}; k=${similar.k}; metrica ${similar.metrica}`,
      stats(indice ? (indice.structure === "ivf" ? 1 : 0) : paginas(total), examinados, filasElegidas.length),
    );
    knn.metric = similar.metrica;
    acceso = paso(
      "fetch", tabla!.info.storage, nombreTabla, null,
      "lee por RID conservando el orden de similitud",
      stats(filasElegidas.length, filasElegidas.length, filasElegidas.length),
      [knn],
    );
  }

  let raiz = acceso;
  let elegidas = filasElegidas;

  if (ordenPuntaje) {
    const direccion = ordenPuntaje.descendente ? "DESC" : "ASC";
    let detalle: string;
    let conserva: boolean;
    if (donde!.tipo === "similar") {
      const metrica = (donde as Similar).metrica;
      conserva = !ordenPuntaje.descendente === MENOR_ES_MEJOR[metrica];
      detalle =
        `SIMILARITY_SCORE ${direccion}; valor natural ${metrica}; ` +
        `${MENOR_ES_MEJOR[metrica] ? "menor es mejor" : "mayor es mejor"}; ` +
        `${conserva ? "conserva" : "invierte"} en memoria el orden de vecinos; sin external sort`;
    } else {
      conserva = ordenPuntaje.descendente;
      detalle = conserva
        ? "por relevancia DESC: el indice ya entrega este orden; sin external sort"
        : "por relevancia ASC: invierte en memoria el orden del indice; sin external sort";
    }
    if (!conserva) elegidas = [...elegidas].reverse();
    raiz = paso("sort", "memory", nombreTabla, null, detalle, {
      pages_read: 0, pages_written: 0, records_examined: conserva ? 0 : elegidas.length, records_returned: elegidas.length,
    }, [raiz]);
  } else if (ordenColumna) {
    const { posicion, descendente } = ordenColumna;
    const signo = descendente ? -1 : 1;
    elegidas = [...elegidas].sort(
      (a, b) => signo * comparar(tabla!.filas[a.fila][posicion], tabla!.filas[b.fila][posicion]) || a.fila - b.fila,
    );
    raiz = pasoDeOrden(tabla!, nombres[posicion], descendente, elegidas.length, raiz);
  }

  // Proyeccion: `*` se expande en su lugar y el puntaje va donde se escribio.
  const salida: { nombre: string; tipo: DataType; valor: (fila: number, puntaje: number) => CellValue }[] = [];
  for (const p of proyecciones) {
    if (p.tipo === "todo") {
      tabla!.info.columns.forEach((c, i) =>
        salida.push({ nombre: c.name, tipo: tabla!.tipos[i], valor: (fila) => tabla!.filas[fila][i] }),
      );
    } else if (p.tipo === "puntaje") {
      salida.push({ nombre: nombrePuntaje!, tipo: "DOUBLE", valor: (_, puntaje) => puntaje });
    } else if (p.tipo === "columna") {
      const i = columnas.find((c) => c.ref === p.ref)!.posicion;
      salida.push({ nombre: nombres[i], tipo: tabla!.tipos[i], valor: (fila) => tabla!.filas[fila][i] });
    }
  }

  let filas = elegidas.map(({ fila, puntaje }) => salida.map((s) => s.valor(fila, puntaje)));
  const soloTodo = proyecciones.length === 1 && proyecciones[0].tipo === "todo";
  if (!soloTodo) {
    raiz = paso(
      "project", "memory", nombreTabla, null,
      salida.map((s) => s.nombre).join(", "),
      stats(0, filas.length, filas.length),
      [raiz],
    );
  }

  if (limite !== null) {
    const antes = filas.length;
    filas = filas.slice(0, limite);
    raiz = paso(
      "limit", "memory", nombreTabla, null, `maximo ${limite} filas`,
      stats(0, Math.min(limite, antes), filas.length),
      [raiz],
    );
  }

  return {
    columns: salida.map((s) => s.nombre),
    column_types: salida.map((s) => s.tipo),
    rows: filas,
    affected_rows: 0,
    plan: armarPlan(sql, raiz),
    spatial_context: null,
  };
}
