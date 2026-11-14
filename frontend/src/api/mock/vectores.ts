/**
 * k-NN vectorial sobre los datos falsos (Parte 4).
 *
 * El puntaje que devuelve SIMILARITY_SCORE() es el "valor natural" de cada
 * metrica, el mismo que el motor: la distancia para `euclidean` (menor es
 * mejor), el coseno y el producto punto para `cosine` y `dot` (mayor es
 * mejor). Los vecinos salen siempre del mejor al peor.
 *
 * La busqueda es exacta aunque el plan diga HNSW o IVF: con 50 vectores un
 * indice aproximado devolveria lo mismo, y simular su error de recall solo
 * haria que la demo pareciera fallar al azar.
 */

import type { Metrica } from "@/api/mock/datos";

function punto(a: number[], b: number[]): number {
  return a.reduce((suma, x, i) => suma + x * b[i], 0);
}

export function puntaje(a: number[], b: number[], metrica: Metrica): number {
  if (metrica === "euclidean") return Math.hypot(...a.map((x, i) => x - b[i]));
  if (metrica === "dot") return punto(a, b);
  const normas = Math.hypot(...a) * Math.hypot(...b);
  return normas === 0 ? 0 : punto(a, b) / normas;
}

export const MENOR_ES_MEJOR: Record<Metrica, boolean> = {
  euclidean: true,
  cosine: false,
  dot: false,
};

export interface Vecino {
  fila: number;
  puntaje: number;
}

/** Los k vecinos de `consulta`, del mas parecido al menos parecido. */
export function vecinos(
  vectores: (number[] | null)[],
  consulta: number[],
  k: number,
  metrica: Metrica,
): Vecino[] {
  const signo = MENOR_ES_MEJOR[metrica] ? 1 : -1;
  return vectores
    .flatMap((v, fila) => (v ? [{ fila, puntaje: puntaje(v, consulta, metrica) }] : []))
    .sort((a, b) => signo * (a.puntaje - b.puntaje) || a.fila - b.fila)
    .slice(0, k);
}
