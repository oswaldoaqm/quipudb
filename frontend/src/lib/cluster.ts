/**
 * Lo que dice la cabecera del frontend sobre el motor (#294). Sin React, para
 * probarlo con `node --test`.
 */

import type { ClusterHealth } from "@/api/types";

export interface Cabecera {
  texto: string;
  /** Una linea por nodo, para el `title` de la etiqueta. */
  detalle: string | null;
  /** Falso si falta algun nodo: la etiqueta se pinta en ambar. */
  completo: boolean;
}

export function cabeceraDe(datosFalsos: boolean, salud: ClusterHealth | null): Cabecera {
  if (datosFalsos) return { texto: "datos falsos", detalle: null, completo: true };
  if (salud === null) return { texto: "motor conectado", detalle: null, completo: true };
  const listos = salud.nodes.filter((n) => n.ready).length;
  const total = salud.nodes.length;
  return {
    texto: `coordinador · ${listos} de ${total} ${total === 1 ? "nodo listo" : "nodos listos"}`,
    detalle: salud.nodes
      .map((n) =>
        n.ready
          ? `${n.node_id} (${n.base_url}): listo, ${n.fragment_ids.join(", ") || "sin fragmentos"}`
          : `${n.node_id} (${n.base_url}): ${n.reason ?? "no listo"}`,
      )
      .join("\n"),
    completo: listos === total,
  };
}
