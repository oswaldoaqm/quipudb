/**
 * Ranking de MATCH sobre los datos falsos (Parte 3).
 *
 * Las formulas son las del core: BM25 con k1 = 1.2, b = 0.75 y el idf
 * suavizado de `bm25_ranking.hpp`; TF-IDF con tf logaritmico, idf en base 10
 * y coseno, como `text_weights.hpp`. Lo que NO es igual es la tokenizacion:
 * el core usa el stemmer Snowball, y aqui basta con quitar tildes, stopwords y
 * la `s` final. Los puntajes se parecen a los del motor, pero no se comparan
 * contra el: para eso esta el banco de pruebas de la Parte 3.
 */

const STOPWORDS = new Set([
  // espanol
  "a", "al", "con", "como", "de", "del", "el", "en", "entre", "es", "esta",
  "la", "las", "lo", "los", "mas", "o", "para", "pero", "por", "que", "se",
  "sin", "sobre", "su", "sus", "un", "una", "uno", "y", "ya",
  // ingles
  "an", "and", "are", "for", "in", "is", "of", "on", "or", "the", "to", "with",
]);

const K1 = 1.2;
const B = 0.75;

/** Lo que el indice ve de un texto: terminos normalizados, en orden. */
export function tokenizar(texto: string): string[] {
  return texto
    .normalize("NFD")
    .replace(/\p{Mn}/gu, "")
    .toLowerCase()
    .split(/[^a-z0-9]+/)
    .filter((t) => t !== "" && !STOPWORDS.has(t))
    .map((t) => (t.length > 3 && t.endsWith("s") ? t.slice(0, -1) : t));
}

function contar(terminos: string[]): Map<string, number> {
  const frecuencias = new Map<string, number>();
  for (const t of terminos) frecuencias.set(t, (frecuencias.get(t) ?? 0) + 1);
  return frecuencias;
}

export type Ranking = "bm25" | "tf_idf";

export interface Coincidencia {
  /** Posicion de la fila en la tabla. */
  fila: number;
  puntaje: number;
}

export interface Busqueda {
  /** Por relevancia descendente; el empate lo rompe la posicion en la tabla. */
  coincidencias: Coincidencia[];
  /** Postings recorridos: la suma del df de los terminos de la consulta. */
  postings: number;
  /** Terminos distintos de la consulta que el indice tuvo que abrir. */
  terminos: number;
}

/** Puntua cada documento que contiene al menos un termino de la consulta. */
export function buscarTexto(
  documentos: string[],
  consulta: string,
  ranking: Ranking,
): Busqueda {
  const docs = documentos.map((d) => contar(tokenizar(d)));
  const largos = documentos.map((d) => tokenizar(d).length);
  const n = docs.length;
  const promedio = largos.reduce((a, b) => a + b, 0) / Math.max(1, n);

  const pedidos = contar(tokenizar(consulta));
  const df = new Map<string, number>();
  for (const termino of pedidos.keys()) {
    df.set(termino, docs.filter((d) => d.has(termino)).length);
  }

  const idfTfIdf = (t: string) => {
    const f = df.get(t) ?? 0;
    return f === 0 ? 0 : Math.log10(n / f);
  };
  const pesoTf = (tf: number) => (tf > 0 ? 1 + Math.log10(tf) : 0);

  // La norma del documento usa TODOS sus terminos, no solo los de la consulta:
  // por eso el coseno queda en [0, 1].
  const dfGlobal = new Map<string, number>();
  if (ranking === "tf_idf") {
    for (const d of docs) for (const t of d.keys()) dfGlobal.set(t, (dfGlobal.get(t) ?? 0) + 1);
  }
  const normaConsulta = Math.hypot(
    ...[...pedidos].map(([t, qtf]) => pesoTf(qtf) * idfTfIdf(t)),
  );

  const coincidencias: Coincidencia[] = [];
  docs.forEach((d, fila) => {
    const presentes = [...pedidos.keys()].filter((t) => d.has(t));
    if (presentes.length === 0) return;

    let puntaje = 0;
    if (ranking === "bm25") {
      for (const t of presentes) {
        const f = df.get(t)!;
        const idf = Math.log(1 + (n - f + 0.5) / (f + 0.5));
        const tf = d.get(t)!;
        puntaje +=
          pedidos.get(t)! * idf * ((tf * (K1 + 1)) / (tf + K1 * (1 - B + (B * largos[fila]) / promedio)));
      }
    } else {
      const normaDoc = Math.hypot(
        ...[...d].map(([t, tf]) => pesoTf(tf) * Math.log10(n / dfGlobal.get(t)!)),
      );
      const producto = presentes.reduce(
        (suma, t) => suma + pesoTf(pedidos.get(t)!) * idfTfIdf(t) * pesoTf(d.get(t)!) * idfTfIdf(t),
        0,
      );
      puntaje = normaDoc > 0 && normaConsulta > 0 ? producto / (normaDoc * normaConsulta) : 0;
    }
    coincidencias.push({ fila, puntaje });
  });

  coincidencias.sort((a, b) => b.puntaje - a.puntaje || a.fila - b.fila);
  return {
    coincidencias,
    postings: [...df.values()].reduce((a, b) => a + b, 0),
    terminos: pedidos.size,
  };
}
