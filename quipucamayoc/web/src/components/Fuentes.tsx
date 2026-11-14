/**
 * Los chunks que se le enviaron a Gemini, numerados como sus citas [n].
 * Una cita de la respuesta abre y resalta su fuente.
 */

import { useEffect, useRef } from "react";

import type { Fuente } from "@/api/types";

export function Fuentes({
  fuentes,
  seleccionada,
  alElegir,
}: {
  fuentes: Fuente[];
  seleccionada: number | null;
  alElegir: (numero: number | null) => void;
}) {
  const refs = useRef(new Map<number, HTMLLIElement>());

  useEffect(() => {
    if (seleccionada !== null) refs.current.get(seleccionada)?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [seleccionada]);

  if (!fuentes.length) return <p className="text-sm text-stone-500">Sin fuentes.</p>;

  return (
    <ol className="space-y-2">
      {fuentes.map((f) => {
        const abierta = seleccionada === f.numero;
        return (
          <li
            key={f.numero}
            ref={(nodo) => {
              if (nodo) refs.current.set(f.numero, nodo);
            }}
            className={`rounded-lg border transition-colors ${abierta ? "border-sky-400 bg-sky-50" : "border-stone-200 bg-white"}`}
          >
            <button
              type="button"
              onClick={() => alElegir(abierta ? null : f.numero)}
              className="flex w-full items-start gap-2 px-3 py-2 text-left"
              aria-expanded={abierta}
            >
              <span
                className={`mt-0.5 flex size-6 shrink-0 items-center justify-center rounded text-xs font-semibold ${f.citada ? "bg-sky-600 text-white" : "bg-stone-200 text-stone-700"}`}
                title={f.citada ? "Citada en la respuesta" : "Enviada a Gemini, no citada"}
              >
                {f.numero}
              </span>
              <span className="min-w-0 flex-1">
                <span className="line-clamp-2 text-sm font-medium text-stone-900">{f.paper.titulo}</span>
                <span className="block truncate text-xs text-stone-600">
                  {f.paper.autores} · {f.paper.anio} · pág. {f.pagina}
                </span>
              </span>
              <span className="flex shrink-0 flex-col items-end gap-1 text-xs text-stone-500" title="Puntaje en QuipuDB">
                {f.relevancia < 1 ? f.relevancia.toFixed(3) : f.relevancia.toFixed(2)}
                <span className="flex gap-1">
                  {f.origen.map((o) => (
                    <span
                      key={o}
                      className="rounded border border-stone-300 px-1 text-[0.65rem] text-stone-600"
                      title={o === "texto" ? "Encontrado por palabras (índice invertido)" : "Encontrado por significado (HNSW)"}
                    >
                      {o === "texto" ? "palabras" : "significado"}
                    </span>
                  ))}
                </span>
              </span>
            </button>
            {abierta && (
              <div className="space-y-2 border-t border-stone-200 px-3 py-2 text-sm">
                <p className="whitespace-pre-line text-stone-700">{f.texto}</p>
                <p className="text-xs text-stone-500">
                  chunk {f.chunk_id} (n.º {f.orden} del paper) ·{" "}
                  {f.paper.url ? (
                    <a href={f.paper.url} target="_blank" rel="noreferrer" className="text-sky-700 underline">
                      arXiv:{f.paper.arxiv_id}
                    </a>
                  ) : (
                    <span>subido a mano</span>
                  )}
                </p>
              </div>
            )}
          </li>
        );
      })}
    </ol>
  );
}
