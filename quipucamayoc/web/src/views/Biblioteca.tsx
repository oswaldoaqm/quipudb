/**
 * Biblioteca (#250): que papers conoce Quipucamayoc, filtrados en QuipuDB, y
 * agregar uno nuevo desde un PDF.
 */

import { useEffect, useState } from "react";

import { agregarPaper, listarPapers, ServidorError } from "@/api/client";
import type { Agregado, Ficha, FiltrosPapers, Sql } from "@/api/types";
import { ListaSql } from "@/components/ListaSql";

function numero(valor: string): number | undefined {
  return valor.trim() === "" ? undefined : Number(valor);
}

function Subir({ alAgregar }: { alAgregar: () => void }) {
  const [archivo, setArchivo] = useState<File | null>(null);
  const [titulo, setTitulo] = useState("");
  const [autores, setAutores] = useState("");
  const [anio, setAnio] = useState(String(new Date().getFullYear()));
  const [abstract, setAbstract] = useState("");
  const [estado, setEstado] = useState<"quieto" | "subiendo" | "listo" | "error">("quieto");
  const [resultado, setResultado] = useState<Agregado | null>(null);
  const [error, setError] = useState("");

  const subir = async () => {
    if (!archivo) return;
    setEstado("subiendo");
    setError("");
    try {
      const agregado = await agregarPaper({ archivo, titulo, autores, anio: Number(anio), abstract });
      setResultado(agregado);
      setEstado("listo");
      setArchivo(null);
      setTitulo("");
      setAutores("");
      setAbstract("");
      alAgregar();
    } catch (e) {
      setError(e instanceof ServidorError ? e.message : String(e));
      setEstado("error");
    }
  };

  const campo = "w-full rounded-md border border-stone-300 px-2.5 py-1.5 text-sm outline-none focus:border-sky-500";
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        void subir();
      }}
      className="space-y-3 rounded-xl border border-stone-200 bg-white p-4 shadow-sm"
    >
      <h2 className="font-semibold text-stone-900">Agregar un paper</h2>
      <p className="text-xs text-stone-500">
        Pasa por la misma ingesta del corpus: extracción del texto, chunks y carga atómica en QuipuDB. Si el PDF
        falla, no queda nada a medias.
      </p>
      <input
        type="file"
        accept="application/pdf,.pdf"
        onChange={(e) => setArchivo(e.target.files?.[0] ?? null)}
        className="block w-full text-sm text-stone-700 file:mr-3 file:rounded-md file:border-0 file:bg-stone-900 file:px-3 file:py-1.5 file:text-white"
        required
      />
      <input value={titulo} onChange={(e) => setTitulo(e.target.value)} placeholder="Título" maxLength={300} required className={campo} />
      <input value={autores} onChange={(e) => setAutores(e.target.value)} placeholder="Autores, separados por comas" required className={campo} />
      <input type="number" value={anio} onChange={(e) => setAnio(e.target.value)} min={1900} max={2100} required className={campo} />
      <textarea value={abstract} onChange={(e) => setAbstract(e.target.value)} placeholder="Abstract (opcional)" rows={3} maxLength={2000} className={campo} />
      <button
        type="submit"
        disabled={!archivo || estado === "subiendo"}
        className="w-full rounded-md bg-sky-700 px-3 py-1.5 text-sm font-medium text-white hover:bg-sky-800 disabled:bg-stone-300"
      >
        {estado === "subiendo" ? "Extrayendo y cargando…" : "Agregar a QuipuDB"}
      </button>
      {estado === "subiendo" && (
        <p className="text-xs text-stone-500" role="status">
          Al final se reconstruye el índice de texto: la siguiente pregunta ya encuentra el paper sin esperar.
        </p>
      )}
      {estado === "listo" && resultado && (
        <p className="rounded-md bg-emerald-50 p-2 text-xs text-emerald-900" role="status">
          ✓ Cargado como <code>{resultado.arxiv_id}</code>: {resultado.chunks} chunks. Extracción{" "}
          {resultado.tiempos.extraccion_s.toFixed(1)} s, carga {resultado.tiempos.carga_s.toFixed(2)} s, índice reconstruido en{" "}
          {resultado.tiempos.indice_s.toFixed(1)} s.
          {resultado.vectores > 0 && ` ${resultado.vectores} embeddings: la búsqueda por significado también lo encuentra.`}
          {resultado.aviso && <span className="block text-amber-800">{resultado.aviso}</span>}
        </p>
      )}
      {estado === "error" && (
        <p className="rounded-md bg-red-50 p-2 text-xs text-red-800" role="alert">
          No se cargó: {error}
        </p>
      )}
    </form>
  );
}

export function Biblioteca() {
  const [desde, setDesde] = useState("");
  const [hasta, setHasta] = useState("");
  const [autor, setAutor] = useState("");
  const [filtros, setFiltros] = useState<FiltrosPapers>({});
  const [papers, setPapers] = useState<Ficha[] | null>(null);
  const [sql, setSql] = useState<Sql[]>([]);
  const [error, setError] = useState("");
  const [abierto, setAbierto] = useState<number | null>(null);
  const [version, setVersion] = useState(0);
  const [cargando, setCargando] = useState(true);

  useEffect(() => {
    // La lista anterior se queda a la vista mientras llega la nueva.
    let vigente = true;
    setCargando(true);
    listarPapers(filtros)
      .then((r) => {
        if (!vigente) return;
        setPapers(r.papers);
        setSql(r.sql);
        setError("");
      })
      .catch((e: unknown) => vigente && setError(e instanceof ServidorError ? e.message : String(e)))
      .finally(() => vigente && setCargando(false));
    return () => {
      vigente = false;
    };
  }, [filtros, version]);

  const total = papers?.reduce((suma, p) => suma + p.chunks, 0) ?? 0;

  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_22rem]">
      <section className="min-w-0 space-y-4">
        <form
          onSubmit={(e) => {
            e.preventDefault();
            setFiltros({ anio_desde: numero(desde), anio_hasta: numero(hasta), autor: autor.trim() || undefined });
          }}
          className="flex flex-wrap items-end gap-3 rounded-xl border border-stone-200 bg-white p-3 text-sm shadow-sm"
        >
          <label className="space-y-1">
            <span className="block text-xs text-stone-600">Año desde</span>
            <input type="number" value={desde} onChange={(e) => setDesde(e.target.value)} className="w-24 rounded border border-stone-300 px-2 py-1" />
          </label>
          <label className="space-y-1">
            <span className="block text-xs text-stone-600">hasta</span>
            <input type="number" value={hasta} onChange={(e) => setHasta(e.target.value)} className="w-24 rounded border border-stone-300 px-2 py-1" />
          </label>
          <label className="min-w-40 flex-1 space-y-1">
            <span className="block text-xs text-stone-600">Autor contiene</span>
            <input value={autor} onChange={(e) => setAutor(e.target.value)} className="w-full rounded border border-stone-300 px-2 py-1" />
          </label>
          <button type="submit" className="rounded-md bg-stone-900 px-3 py-1.5 text-white hover:bg-stone-700">
            Filtrar
          </button>
          <button
            type="button"
            onClick={() => {
              setDesde("");
              setHasta("");
              setAutor("");
              setFiltros({});
            }}
            className="rounded-md border border-stone-300 px-3 py-1.5 text-stone-700 hover:bg-stone-100"
          >
            Limpiar
          </button>
          <p className="basis-full text-xs text-stone-500">
            El rango de años lo resuelve QuipuDB con el B+ sobre <code>anio</code>; el autor se filtra en el servidor,
            porque el SQL del motor no tiene <code>LIKE</code>.
          </p>
        </form>

        {error && (
          <p className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-800" role="alert">
            {error}
          </p>
        )}
        {!papers && !error && <p className="text-sm text-stone-500">Consultando QuipuDB…</p>}
        {papers && (
          <div className="overflow-hidden rounded-xl border border-stone-200 bg-white shadow-sm">
            <p className="border-b border-stone-200 px-4 py-2 text-sm text-stone-600">
              {papers.length} papers · {total.toLocaleString("es-PE")} chunks
              {cargando && <span className="ml-2 text-stone-400">actualizando…</span>}
            </p>
            {papers.length === 0 ? (
              <p className="px-4 py-6 text-sm text-stone-500">Ningún paper cumple los filtros.</p>
            ) : (
              <ul className="max-h-[60vh] divide-y divide-stone-100 overflow-y-auto">
                {papers.map((p) => (
                  <li key={p.id}>
                    <button
                      type="button"
                      onClick={() => setAbierto(abierto === p.id ? null : p.id)}
                      className="flex w-full items-start gap-3 px-4 py-2.5 text-left hover:bg-stone-50"
                      aria-expanded={abierto === p.id}
                    >
                      <span className="w-10 shrink-0 text-sm tabular-nums text-stone-500">{p.anio}</span>
                      <span className="min-w-0 flex-1">
                        <span className="block text-sm font-medium text-stone-900">{p.titulo}</span>
                        <span className="block truncate text-xs text-stone-600">{p.autores}</span>
                      </span>
                      <span className="shrink-0 text-xs text-stone-500">{p.categoria}</span>
                      <span className="w-16 shrink-0 text-right text-xs tabular-nums text-stone-600">{p.chunks} chunks</span>
                    </button>
                    {abierto === p.id && (
                      <div className="space-y-2 bg-stone-50 px-4 py-3 pl-[4.25rem] text-sm">
                        <p className="text-stone-700">{p.abstract || "Sin abstract."}</p>
                        <p className="text-xs text-stone-500">
                          id {p.id} ·{" "}
                          {p.url ? (
                            <a href={p.url} target="_blank" rel="noreferrer" className="text-sky-700 underline">
                              arXiv:{p.arxiv_id}
                            </a>
                          ) : (
                            `${p.arxiv_id} (subido a mano)`
                          )}
                        </p>
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}

        {sql.length > 0 && (
          <section className="space-y-2">
            <h2 className="text-sm font-semibold text-stone-900">SQL que se ejecutó en QuipuDB</h2>
            <ListaSql sql={sql} />
          </section>
        )}
      </section>

      <aside>
        <Subir alAgregar={() => setVersion((v) => v + 1)} />
      </aside>
    </div>
  );
}
