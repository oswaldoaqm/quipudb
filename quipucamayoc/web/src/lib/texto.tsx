/**
 * El Markdown minimo que escribe Gemini: parrafos, listas, **negrita**,
 * *cursiva*, `codigo`, $formulas$ y citas [n].
 *
 * Se arma con nodos de React y nunca con innerHTML: el texto viene de un LLM
 * que a su vez leyo PDFs de terceros, y no tiene por que ejecutarse nada.
 */

import type { ReactNode } from "react";

import { bloques, partes } from "@/lib/markdown";

export function enLinea(texto: string, cita: (numero: number) => ReactNode): ReactNode[] {
  return partes(texto).map(({ texto: parte, marca }, i) => {
    if (!marca) return <span key={i}>{parte}</span>;
    if (parte.startsWith("**") && parte.endsWith("**")) return <strong key={i}>{enLinea(parte.slice(2, -2), cita)}</strong>;
    if (/^\[\d+\]$/.test(parte)) return <span key={i}>{cita(Number(parte.slice(1, -1)))}</span>;
    if (parte.startsWith("`") || parte.startsWith("$")) {
      return (
        <code key={i} className="rounded bg-stone-100 px-1 py-0.5 font-mono text-[0.85em] text-stone-800">
          {parte.slice(1, -1)}
        </code>
      );
    }
    return <em key={i}>{parte.slice(1, -1)}</em>;
  });
}

export function Markdown({ texto, cita }: { texto: string; cita: (numero: number) => ReactNode }) {
  return (
    <div className="space-y-3 leading-relaxed text-stone-800">
      {bloques(texto).map((b, i) => {
        if (b.tipo === "titulo") return <h3 key={i} className="font-semibold text-stone-900">{enLinea(b.texto, cita)}</h3>;
        if (b.tipo === "parrafo") return <p key={i}>{enLinea(b.texto, cita)}</p>;
        const Lista = b.ordenada ? "ol" : "ul";
        return (
          <Lista key={i} className={`space-y-1.5 pl-5 ${b.ordenada ? "list-decimal" : "list-disc"}`}>
            {b.items.map((item, j) => (
              <li key={j}>{enLinea(item, cita)}</li>
            ))}
          </Lista>
        );
      })}
    </div>
  );
}
