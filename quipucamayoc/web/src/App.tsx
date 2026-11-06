import { useEffect, useState } from "react";

import { obtenerSalud, ServidorError } from "@/api/client";
import type { Health } from "@/api/types";

function Estado({ ok, children }: { ok: boolean; children: React.ReactNode }) {
  return (
    <li className="flex items-center gap-2">
      <span className={`size-2.5 rounded-full ${ok ? "bg-emerald-500" : "bg-amber-500"}`} />
      {children}
    </li>
  );
}

export default function App() {
  const [salud, setSalud] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    obtenerSalud()
      .then(setSalud)
      .catch((e: unknown) =>
        setError(e instanceof ServidorError ? e.message : "Error inesperado"),
      );
  }, []);

  return (
    <main className="mx-auto max-w-2xl p-8 font-sans text-slate-800">
      <h1 className="text-2xl font-semibold">Quipucamayoc</h1>
      <p className="mt-1 text-slate-500">
        Preguntas sobre papers académicos, respondidas con QuipuDB y Gemini.
      </p>
      <section className="mt-6 rounded-lg border border-slate-200 p-4">
        <h2 className="font-medium">Estado</h2>
        {error && <p className="mt-2 text-red-600">{error}</p>}
        {!error && !salud && <p className="mt-2 text-slate-500">Consultando…</p>}
        {salud && (
          <ul className="mt-2 space-y-1 text-sm">
            <Estado ok={salud.engine.reachable}>
              Motor en {salud.engine.url}:{" "}
              {salud.engine.reachable
                ? `${salud.engine.tables} tablas`
                : `sin respuesta (${salud.engine.error})`}
            </Estado>
            <Estado ok={salud.llm.configured}>
              Gemini ({salud.llm.model}):{" "}
              {salud.llm.configured ? "configurado" : "falta GEMINI_API_KEY en quipucamayoc/.env"}
            </Estado>
          </ul>
        )}
      </section>
    </main>
  );
}
