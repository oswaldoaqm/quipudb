/**
 * El Markdown minimo que escribe Gemini: parrafos, listas, **negrita**,
 * *cursiva*, `codigo`, $formulas$ y citas [n].
 *
 * Se arma con nodos de React y nunca con innerHTML: el texto viene de un LLM
 * que a su vez leyo PDFs de terceros, y no tiene por que ejecutarse nada.
 */

import type { ReactNode } from "react";

import { arbol, bloques, type Nodo } from "@/lib/markdown";

function dibujar(nodos: Nodo[], cita: (numero: number) => ReactNode): ReactNode[] {
  return nodos.map((nodo, i) => {
    switch (nodo.tipo) {
      case "texto":
        return <span key={i}>{nodo.texto}</span>;
      case "cita":
        return <span key={i}>{cita(nodo.numero)}</span>;
      case "codigo":
        return (
          <code key={i} className="rounded bg-stone-100 px-1 py-0.5 font-mono text-[0.85em] text-stone-800">
            {nodo.texto}
          </code>
        );
      case "negrita":
        return <strong key={i}>{dibujar(nodo.hijos, cita)}</strong>;
      case "cursiva":
        return <em key={i}>{dibujar(nodo.hijos, cita)}</em>;
    }
  });
}

export function enLinea(texto: string, cita: (numero: number) => ReactNode): ReactNode[] {
  return dibujar(arbol(texto), cita);
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
