/**
 * Texto de un fragmento para el panel de Archivos (#295). Sin React, para
 * probarlo con `node --test`.
 */

import type { FragmentStatus } from "@/api/types";

/** `id < 10000`, `10000 ≤ id < 20000`, `id ≥ 20000` o `tabla completa`. */
export function rangoDe(clave: string, lower: number | null, upper: number | null): string {
  const n = (v: number) => v.toLocaleString("es-PE");
  if (lower === null && upper === null) return "tabla completa";
  if (lower === null) return `${clave} < ${n(upper!)}`;
  if (upper === null) return `${clave} ≥ ${n(lower)}`;
  return `${n(lower)} ≤ ${clave} < ${n(upper)}`;
}

/** El conteo, o por que no lo hay. */
export function conteoDe(fragmento: FragmentStatus): string {
  if (!fragmento.available || fragmento.record_count === null) return "nodo no disponible";
  const filas = fragmento.record_count;
  return `${filas.toLocaleString("es-PE")} ${filas === 1 ? "fila" : "filas"}`;
}
