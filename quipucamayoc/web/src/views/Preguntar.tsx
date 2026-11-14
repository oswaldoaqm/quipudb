/**
 * Preguntar (#248): la pregunta, la respuesta con citas, sus fuentes y como
 * la resolvio la base de datos (#249). Las etapas llegan por /ask/stream, asi
 * que las fuentes aparecen mientras Gemini todavia escribe.
 */

import { useRef, useState } from "react";

import { preguntar, ServidorError } from "@/api/client";
import type { AskResponse, Fuente, Modo, Origen, Ranking, Sql } from "@/api/types";
import { BarraTiempos } from "@/components/BarraTiempos";
import { Fuentes } from "@/components/Fuentes";
import { ListaSql } from "@/components/ListaSql";
import { Markdown } from "@/lib/texto";

type Etapa = "reescribiendo" | "buscando" | "generando" | "lista" | "error";

interface Turno {
  id: number;
  pregunta: string;
  ranking: Ranking;
  modo: Modo;
  etapa: Etapa;
  consulta?: string;
  fuentes: Fuente[];
  sql: Sql[];
  respuesta?: AskResponse;
  error?: { mensaje: string; origen: Origen | "servidor" };
}

const MENSAJES_ETAPA: Record<Exclude<Etapa, "lista" | "error">, string> = {
  reescribiendo: "Gemini está preparando la pregunta para buscarla…",
  buscando: "Buscando en QuipuDB con el índice invertido…",
  generando: "Gemini está escribiendo la respuesta con estas fuentes…",
};

const ORIGENES: Record<Origen | "servidor", string> = {
  motor: "QuipuDB",
  llm: "Gemini",
  servidor: "Servidor de Quipucamayoc",
};

const EJEMPLOS = [
  "¿Cuáles son las ventajas de HNSW sobre IVF?",
  "¿Qué es un índice aprendido y qué reemplaza?",
  "¿Cómo funciona la cuantización de productos?",
  "¿Por qué BM25 satura la frecuencia de los términos?",
];

function Cargando({ etapa }: { etapa: Exclude<Etapa, "lista" | "error"> }) {
  const pasos: Exclude<Etapa, "lista" | "error">[] = ["reescribiendo", "buscando", "generando"];
  const actual = pasos.indexOf(etapa);
  return (
    <div className="space-y-2" role="status" aria-live="polite">
      <ol className="flex gap-1.5">
        {pasos.map((p, i) => (
          <li key={p} className={`h-1 flex-1 rounded-full ${i < actual ? "bg-sky-600" : i === actual ? "animate-pulse bg-sky-400" : "bg-stone-200"}`} />
        ))}
      </ol>
      <p className="text-sm text-stone-600">{MENSAJES_ETAPA[etapa]}</p>
    </div>
  );
}

const MODOS: { modo: Modo; nombre: string; ayuda: string }[] = [
  { modo: "texto", nombre: "Palabras", ayuda: "BM25 o TF-IDF sobre el índice invertido (Parte 3)" },
  { modo: "vectores", nombre: "Significado", ayuda: "HNSW con el embedding de la pregunta (Parte 4)" },
  { modo: "hibrido", nombre: "Híbrido", ayuda: "Los dos, fusionados con Reciprocal Rank Fusion" },
];

export function Preguntar({ busquedaVectorial }: { busquedaVectorial: boolean }) {
  const [texto, setTexto] = useState("");
  const [ranking, setRanking] = useState<Ranking>("BM25");
  // Sin elegir, el hibrido si hay embeddings cargados; si no, solo palabras.
  const [modoElegido, setModo] = useState<Modo | null>(null);
  const modo: Modo = busquedaVectorial ? (modoElegido ?? "hibrido") : "texto";
  const [k, setK] = useState(8);
  const [reescribir, setReescribir] = useState(true);
  const [turnos, setTurnos] = useState<Turno[]>([]);
  const [actualId, setActualId] = useState<number | null>(null);
  const [fuenteAbierta, setFuenteAbierta] = useState<number | null>(null);
  const cancelar = useRef<AbortController | null>(null);

  const actual = turnos.find((t) => t.id === actualId) ?? null;
  const ocupado = actual !== null && !["lista", "error"].includes(actual.etapa);

  const actualizar = (id: number, cambio: Partial<Turno>) =>
    setTurnos((ts) => ts.map((t) => (t.id === id ? { ...t, ...cambio } : t)));

  const enviar = async (pregunta: string) => {
    pregunta = pregunta.trim();
    if (!pregunta || ocupado) return;
    const id = Date.now();
    setTurnos((ts) => [
      {
        id,
        pregunta,
        ranking,
        modo,
        etapa: modo !== "texto" || reescribir ? "reescribiendo" : "buscando",
        fuentes: [],
        sql: [],
      },
      ...ts,
    ]);
    setActualId(id);
    setFuenteAbierta(null);
    setTexto("");
    cancelar.current = new AbortController();
    try {
      await preguntar(
        { pregunta, k, ranking, reescribir, modo },
        (evento) => {
          if (evento.etapa === "consulta") actualizar(id, { etapa: "buscando", consulta: evento.consulta });
          else if (evento.etapa === "recuperacion")
            actualizar(id, { etapa: "generando", fuentes: evento.fuentes, sql: evento.sql });
          else if (evento.etapa === "respuesta")
            actualizar(id, {
              etapa: "lista",
              respuesta: evento.respuesta,
              consulta: evento.respuesta.consulta,
              fuentes: evento.respuesta.fuentes,
              sql: evento.respuesta.sql,
            });
          else actualizar(id, { etapa: "error", error: { mensaje: evento.error, origen: evento.origen } });
        },
        cancelar.current.signal,
      );
    } catch (e) {
      if (e instanceof DOMException && e.name === "AbortError") {
        actualizar(id, { etapa: "error", error: { mensaje: "Cancelaste la pregunta.", origen: "servidor" } });
      } else {
        const error = e instanceof ServidorError ? e : new ServidorError(String(e));
        actualizar(id, { etapa: "error", error: { mensaje: error.message, origen: error.origen } });
      }
    }
  };

  const cita = (numero: number) => {
    const fuente = actual?.fuentes.find((f) => f.numero === numero);
    return (
      <button
        type="button"
        onClick={() => setFuenteAbierta(numero)}
        className="mx-0.5 inline-flex h-5 min-w-5 items-center justify-center rounded bg-sky-100 px-1 align-baseline text-[0.7rem] font-semibold text-sky-800 hover:bg-sky-200"
        title={fuente ? `${fuente.paper.autores} (${fuente.paper.anio}). ${fuente.paper.titulo}` : `Fuente ${numero}`}
      >
        {numero}
      </button>
    );
  };

  return (
    <div className="grid gap-6 lg:grid-cols-[16rem_minmax(0,1fr)_24rem]">
      {/* Historial de la sesion */}
      <aside className="order-3 space-y-2 lg:order-1">
        <h2 className="text-xs font-semibold uppercase tracking-wide text-stone-500">Esta sesión</h2>
        {turnos.length === 0 ? (
          <p className="text-sm text-stone-500">Tus preguntas aparecen aquí.</p>
        ) : (
          <ul className="space-y-1">
            {turnos.map((t) => (
              <li key={t.id}>
                <button
                  type="button"
                  onClick={() => {
                    setActualId(t.id);
                    setFuenteAbierta(null);
                  }}
                  className={`w-full rounded-md px-2 py-1.5 text-left text-sm ${t.id === actualId ? "bg-stone-200 text-stone-900" : "text-stone-700 hover:bg-stone-100"}`}
                >
                  <span className="line-clamp-2">{t.pregunta}</span>
                  <span className="text-xs text-stone-500">
                    {t.etapa === "error" ? "error" : t.etapa === "lista" ? `${t.respuesta?.citas.length ?? 0} citas · ${MODOS.find((m) => m.modo === t.modo)?.nombre}` : "en curso…"}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </aside>

      {/* Pregunta y respuesta */}
      <section className="order-1 min-w-0 space-y-4 lg:order-2">
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void enviar(texto);
          }}
          className="space-y-3 rounded-xl border border-stone-200 bg-white p-4 shadow-sm"
        >
          <textarea
            value={texto}
            onChange={(e) => setTexto(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void enviar(texto);
              }
            }}
            rows={2}
            maxLength={1000}
            placeholder="Pregunta sobre índices, bases de datos vectoriales, RAG…"
            className="w-full resize-none rounded-md border border-stone-300 px-3 py-2 text-stone-900 outline-none focus:border-sky-500 focus:ring-2 focus:ring-sky-100"
          />
          <div className="flex flex-wrap items-center gap-3 text-sm">
            <div className="inline-flex rounded-md border border-stone-300 p-0.5" role="radiogroup" aria-label="Buscar por">
              {MODOS.map((m) => {
                const deshabilitado = m.modo !== "texto" && !busquedaVectorial;
                return (
                  <button
                    key={m.modo}
                    type="button"
                    role="radio"
                    aria-checked={modo === m.modo}
                    disabled={deshabilitado}
                    onClick={() => setModo(m.modo)}
                    title={deshabilitado ? "Faltan los embeddings: python -m app.corpus.vectores embeber y cargar" : m.ayuda}
                    className={`rounded px-2.5 py-1 disabled:cursor-not-allowed disabled:text-stone-300 ${modo === m.modo ? "bg-sky-700 text-white" : "text-stone-700 hover:bg-stone-100"}`}
                  >
                    {m.nombre}
                  </button>
                );
              })}
            </div>
            <div
              className={`inline-flex rounded-md border border-stone-300 p-0.5 ${modo === "vectores" ? "opacity-40" : ""}`}
              role="radiogroup"
              aria-label="Ranking"
            >
              {(["BM25", "TF_IDF"] as Ranking[]).map((r) => (
                <button
                  key={r}
                  type="button"
                  role="radio"
                  aria-checked={ranking === r}
                  disabled={modo === "vectores"}
                  onClick={() => setRanking(r)}
                  className={`rounded px-2.5 py-1 ${ranking === r ? "bg-stone-900 text-white" : "text-stone-700 hover:bg-stone-100"}`}
                >
                  {r === "TF_IDF" ? "TF-IDF" : r}
                </button>
              ))}
            </div>
            <label className="flex items-center gap-2 text-stone-700">
              k
              <select
                value={k}
                onChange={(e) => setK(Number(e.target.value))}
                className="rounded border border-stone-300 bg-white px-1.5 py-1"
              >
                {[3, 5, 8, 12, 16, 20].map((n) => (
                  <option key={n} value={n}>
                    {n}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex items-center gap-1.5 text-stone-700" title="Gemini pasa la pregunta a palabras clave en inglés, el idioma de los papers">
              <input
                type="checkbox"
                checked={reescribir && modo !== "vectores"}
                disabled={modo === "vectores"}
                onChange={(e) => setReescribir(e.target.checked)}
              />
              reescribir en inglés
            </label>
            <div className="ml-auto flex gap-2">
              {ocupado && (
                <button
                  type="button"
                  onClick={() => cancelar.current?.abort()}
                  className="rounded-md border border-stone-300 px-3 py-1.5 text-stone-700 hover:bg-stone-100"
                >
                  Cancelar
                </button>
              )}
              <button
                type="submit"
                disabled={ocupado || !texto.trim()}
                className="rounded-md bg-sky-700 px-4 py-1.5 font-medium text-white hover:bg-sky-800 disabled:cursor-not-allowed disabled:bg-stone-300"
              >
                Preguntar
              </button>
            </div>
          </div>
        </form>

        {!actual && (
          <div className="space-y-2">
            <p className="text-sm text-stone-600">Prueba con alguna de estas:</p>
            <div className="flex flex-wrap gap-2">
              {EJEMPLOS.map((ejemplo) => (
                <button
                  key={ejemplo}
                  type="button"
                  onClick={() => void enviar(ejemplo)}
                  className="rounded-full border border-stone-300 bg-white px-3 py-1 text-sm text-stone-700 hover:border-sky-400 hover:text-sky-800"
                >
                  {ejemplo}
                </button>
              ))}
            </div>
          </div>
        )}

        {actual && (
          <article className="space-y-4 rounded-xl border border-stone-200 bg-white p-5 shadow-sm">
            <header className="space-y-1">
              <h2 className="text-lg font-semibold text-stone-900">{actual.pregunta}</h2>
              {actual.consulta && (
                <p className="text-sm text-stone-600">
                  {actual.modo === "vectores" ? (
                    "Buscado en QuipuDB por significado: HNSW con el embedding de la pregunta"
                  ) : (
                    <>
                      Buscado en QuipuDB como{" "}
                      <code className="rounded bg-stone-100 px-1.5 py-0.5 font-mono text-xs text-stone-800">{actual.consulta}</code>{" "}
                      con {actual.ranking === "TF_IDF" ? "TF-IDF" : "BM25"}
                      {actual.modo === "hibrido" && " y por significado con HNSW, fusionados con RRF"}
                    </>
                  )}
                </p>
              )}
            </header>

            {actual.etapa === "error" && actual.error && (
              <div className="rounded-md border border-red-200 bg-red-50 p-3 text-sm" role="alert">
                <p className="font-medium text-red-800">Falló {ORIGENES[actual.error.origen]}</p>
                <p className="text-red-700">{actual.error.mensaje}</p>
              </div>
            )}
            {actual.etapa !== "lista" && actual.etapa !== "error" && <Cargando etapa={actual.etapa} />}
            {actual.respuesta && (
              <>
                <Markdown texto={actual.respuesta.respuesta} cita={cita} />
                {actual.respuesta.citas_descartadas.length > 0 && (
                  <p className="text-xs text-stone-500">
                    Se quitaron citas a fuentes que no existen: {actual.respuesta.citas_descartadas.map((n) => `[${n}]`).join(" ")}
                  </p>
                )}
                <footer className="space-y-3 border-t border-stone-100 pt-3">
                  <BarraTiempos tiempos={actual.respuesta.tiempos} />
                  <p className="text-xs text-stone-500">
                    {actual.respuesta.modelo ? `Respondió ${actual.respuesta.modelo}` : "No hizo falta llamar a Gemini: no hubo fuentes"}
                  </p>
                </footer>
              </>
            )}
          </article>
        )}

        {actual && actual.sql.length > 0 && (
          <section className="space-y-2">
            <h2 className="text-sm font-semibold text-stone-900">Cómo respondió la base de datos</h2>
            <p className="text-xs text-stone-500">
              Cada sentencia que se ejecutó en QuipuDB, con su plan. Los pasos resaltados usan un índice.
            </p>
            <ListaSql sql={actual.sql} />
          </section>
        )}
      </section>

      {/* Fuentes */}
      <aside className="order-2 space-y-2 lg:order-3">
        <h2 className="text-xs font-semibold uppercase tracking-wide text-stone-500">
          Fuentes {actual?.fuentes.length ? `(${actual.fuentes.length})` : ""}
        </h2>
        {actual ? (
          <Fuentes fuentes={actual.fuentes} seleccionada={fuenteAbierta} alElegir={setFuenteAbierta} />
        ) : (
          <p className="text-sm text-stone-500">Los fragmentos de papers que lea Gemini aparecen aquí, con su puntaje en QuipuDB.</p>
        )}
      </aside>
    </div>
  );
}
