/**
 * Como respondio la base de datos (#249): cada SQL que el servidor ejecuto en
 * QuipuDB, en orden, con el plan que devolvio el motor. Es lo que separa la
 * demo de un chatbot cualquiera: se ve el indice invertido y el hash trabajar.
 */

import { useState } from "react";

import type { PasoPlan, Sql } from "@/api/types";
import { formatearMs } from "@/components/BarraTiempos";

const ESTRUCTURAS: Record<string, string> = {
  inverted_index: "índice invertido",
  extendible_hash: "hash extensible",
  bplus_unclustered: "B+ no agrupado",
  bplus_clustered: "B+ agrupado",
  heap: "heap file",
  sequential: "archivo secuencial",
  memory: "memoria",
};

/** Los pasos que tocan un indice: se resaltan en el arbol. */
const DE_INDICE = new Set(["text_search", "index_search", "index_range", "knn_search", "radius_search"]);

function Paso({ paso, nivel }: { paso: PasoPlan; nivel: number }) {
  const indice = DE_INDICE.has(paso.op);
  return (
    <li>
      <div
        className={`rounded-md border px-2.5 py-1.5 text-xs ${indice ? "border-amber-300 bg-amber-50" : "border-stone-200 bg-white"}`}
        style={{ marginLeft: nivel * 14 }}
      >
        <div className="flex flex-wrap items-baseline gap-x-2">
          <span className="font-mono font-semibold text-stone-900">{paso.op}</span>
          <span className="text-stone-600">{ESTRUCTURAS[paso.structure] ?? paso.structure}</span>
          {paso.table && <span className="text-stone-500">· {paso.table}{paso.column ? `.${paso.column}` : ""}</span>}
          <span className="ml-auto text-stone-500">
            {paso.stats.pages_read} pág. · {formatearMs(paso.time_ms)}
          </span>
        </div>
        {paso.detail && <div className="mt-0.5 break-words text-stone-600">{paso.detail}</div>}
      </div>
      {paso.children.length > 0 && (
        <ul className="mt-1 space-y-1">
          {paso.children.map((hijo, i) => (
            <Paso key={i} paso={hijo} nivel={nivel + 1} />
          ))}
        </ul>
      )}
    </li>
  );
}

function Sentencia({ sql, numero }: { sql: Sql; numero: number }) {
  const [abierta, setAbierta] = useState(numero === 1);
  const [copiada, setCopiada] = useState(false);

  const copiar = async () => {
    await navigator.clipboard.writeText(sql.sql);
    setCopiada(true);
    setTimeout(() => setCopiada(false), 1500);
  };

  return (
    <li className="rounded-lg border border-stone-200 bg-stone-50">
      <div className="flex items-center gap-2 px-3 py-2">
        <button
          type="button"
          onClick={() => setAbierta(!abierta)}
          className="flex min-w-0 flex-1 items-center gap-2 text-left text-sm"
          aria-expanded={abierta}
        >
          <span className="text-stone-400">{abierta ? "▾" : "▸"}</span>
          <span className="font-medium text-stone-900">{numero}.</span>
          <span className="truncate text-stone-700">{sql.proposito}</span>
        </button>
        <span className="shrink-0 text-xs text-stone-500">
          {sql.filas} filas · {formatearMs(sql.tiempo_ms)}
        </span>
        <button
          type="button"
          onClick={copiar}
          className="shrink-0 rounded border border-stone-300 bg-white px-2 py-0.5 text-xs text-stone-700 hover:bg-stone-100"
          title="Copia el SQL para ejecutarlo en el Panel de Consultas del motor (localhost:5173)"
        >
          {copiada ? "Copiado" : "Copiar SQL"}
        </button>
      </div>
      {abierta && (
        <div className="space-y-2 border-t border-stone-200 px-3 py-2">
          <pre className="overflow-x-auto whitespace-pre-wrap break-all rounded bg-stone-900 p-2 font-mono text-xs text-stone-100">
            {sql.sql}
          </pre>
          {sql.plan ? (
            <ul className="space-y-1">
              <Paso paso={sql.plan.root} nivel={0} />
            </ul>
          ) : (
            <p className="text-xs text-stone-500">El motor no devolvió plan para esta sentencia.</p>
          )}
        </div>
      )}
    </li>
  );
}

export function ListaSql({ sql }: { sql: Sql[] }) {
  if (!sql.length) return <p className="text-sm text-stone-500">No se ejecutó ninguna consulta.</p>;
  return (
    <ol className="space-y-2">
      {sql.map((s, i) => (
        <Sentencia key={`${i}-${s.sql}`} sql={s} numero={i + 1} />
      ))}
    </ol>
  );
}
