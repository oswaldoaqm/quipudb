import { useEffect, useState } from "react";

import { obtenerSalud, ServidorError, USA_DATOS_FALSOS } from "@/api/client";
import type { Health } from "@/api/types";
import { Biblioteca } from "@/views/Biblioteca";
import { Preguntar } from "@/views/Preguntar";

type Vista = "preguntar" | "biblioteca";

function Indicador({ ok, children, titulo }: { ok: boolean; children: React.ReactNode; titulo: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-stone-200 bg-white px-2.5 py-1 text-xs text-stone-700" title={titulo}>
      <span className={`size-2 rounded-full ${ok ? "bg-emerald-500" : "bg-amber-500"}`} />
      {children}
      <span className="sr-only">{ok ? "(disponible)" : "(no disponible)"}</span>
    </span>
  );
}

export default function App() {
  const [vista, setVista] = useState<Vista>("preguntar");
  const [salud, setSalud] = useState<Health | null>(null);
  const [errorSalud, setErrorSalud] = useState<string | null>(null);

  useEffect(() => {
    obtenerSalud()
      .then(setSalud)
      .catch((e: unknown) => setErrorSalud(e instanceof ServidorError ? e.message : "Error inesperado"));
  }, []);

  return (
    <div className="min-h-screen bg-stone-50 font-sans text-stone-800">
      <header className="border-b border-stone-200 bg-white">
        <div className="mx-auto flex max-w-[90rem] flex-wrap items-center gap-x-6 gap-y-2 px-6 py-3">
          <div>
            <h1 className="text-lg font-semibold text-stone-900">Quipucamayoc</h1>
            <p className="text-xs text-stone-500">Preguntas sobre papers académicos, con QuipuDB y Gemini</p>
          </div>
          <nav className="flex gap-1" aria-label="Vistas">
            {(["preguntar", "biblioteca"] as Vista[]).map((v) => (
              <button
                key={v}
                type="button"
                onClick={() => setVista(v)}
                aria-current={vista === v ? "page" : undefined}
                className={`rounded-md px-3 py-1.5 text-sm ${vista === v ? "bg-stone-900 text-white" : "text-stone-700 hover:bg-stone-100"}`}
              >
                {v === "preguntar" ? "Preguntar" : "Biblioteca"}
              </button>
            ))}
          </nav>
          <div className="ml-auto flex flex-wrap gap-2">
            {USA_DATOS_FALSOS && <span className="rounded-full bg-amber-100 px-2.5 py-1 text-xs text-amber-900">datos falsos</span>}
            {errorSalud && <span className="text-xs text-red-700">{errorSalud}</span>}
            {salud && (
              <>
                <Indicador ok={salud.engine.reachable} titulo={salud.engine.error ?? salud.engine.url}>
                  QuipuDB {salud.engine.reachable ? `· ${salud.engine.tables} tablas` : "sin respuesta"}
                </Indicador>
                <Indicador ok={salud.llm.configured} titulo={salud.llm.configured ? "" : "Falta GEMINI_API_KEY en quipucamayoc/.env"}>
                  {salud.llm.model}
                </Indicador>
              </>
            )}
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-[90rem] px-6 py-6">{vista === "preguntar" ? <Preguntar /> : <Biblioteca />}</main>
    </div>
  );
}
