import { Panel } from "@/components/Panel";
import type { Op, Plan, Step } from "@/api/types";
import { cn } from "@/lib/utils";

interface PlanPanelProps {
  plan: Plan | null;
}

/**
 * Como se resolvio el paso. Es la distincion que pide el criterio 3 del issue
 * #37: un recorrido completo y una busqueda por indice tienen que verse
 * distintos de un vistazo, porque esa diferencia es justo lo que el motor
 * promete y lo que 2.1.6 mide.
 */
type Categoria = "indice" | "recorrido" | "externo" | "memoria" | "escritura";

const CATEGORIA: Record<Op, Categoria> = {
  search: "indice",
  range_search: "indice",
  index_search: "indice",
  index_range: "indice",
  fetch: "indice",
  scan: "recorrido",
  sort: "externo",
  group: "externo",
  join: "externo",
  filter: "memoria",
  project: "memoria",
  limit: "memoria",
  insert: "escritura",
  remove: "escritura",
  update: "escritura",
};

const ETIQUETA: Record<Categoria, string> = {
  indice: "acceso dirigido",
  recorrido: "recorre la tabla entera",
  externo: "algoritmo externo",
  memoria: "en memoria",
  escritura: "escritura",
};

const COLOR: Record<Categoria, string> = {
  indice:
    "border-emerald-300 bg-emerald-50 text-emerald-800 dark:border-emerald-800 dark:bg-emerald-950 dark:text-emerald-300",
  recorrido:
    "border-amber-300 bg-amber-50 text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-300",
  externo:
    "border-violet-300 bg-violet-50 text-violet-800 dark:border-violet-800 dark:bg-violet-950 dark:text-violet-300",
  memoria: "border-border bg-muted text-muted-foreground",
  escritura:
    "border-sky-300 bg-sky-50 text-sky-800 dark:border-sky-800 dark:bg-sky-950 dark:text-sky-300",
};

const BARRA: Record<Categoria, string> = {
  indice: "bg-emerald-500",
  recorrido: "bg-amber-500",
  externo: "bg-violet-500",
  memoria: "bg-muted-foreground/30",
  escritura: "bg-sky-500",
};

interface Nodo {
  paso: Step;
  nivel: number;
  orden: number;
}

/**
 * Numera los pasos en el orden en que se ejecutaron.
 *
 * Es postorden porque el ADR 0002 lo fija asi: los hijos se ejecutan antes que
 * el padre y le entregan su salida. El arbol se dibuja de arriba hacia abajo,
 * pero se ejecuta de abajo hacia arriba, y sin los numeros esa inversion no se
 * ve (criterio 2: "en que orden se ejecutaron las operaciones").
 */
function aplanar(raiz: Step): Nodo[] {
  const orden = new Map<Step, number>();
  let contador = 0;
  const numerar = (paso: Step) => {
    paso.children.forEach(numerar);
    orden.set(paso, ++contador);
  };
  numerar(raiz);

  const filas: Nodo[] = [];
  const recorrer = (paso: Step, nivel: number) => {
    filas.push({ paso, nivel, orden: orden.get(paso) ?? 0 });
    paso.children.forEach((hijo) => recorrer(hijo, nivel + 1));
  };
  recorrer(raiz, 0);
  return filas;
}

function Fila({ nodo, maximoPaginas }: { nodo: Nodo; maximoPaginas: number }) {
  const { paso, nivel, orden } = nodo;
  const categoria = CATEGORIA[paso.op];
  const paginas = paso.stats.pages_read;
  const porcentaje = maximoPaginas > 0 ? (paginas / maximoPaginas) * 100 : 0;

  return (
    <li style={{ paddingLeft: `${nivel * 14}px` }} className="py-0.5">
      <div
        className={cn(
          "rounded-md border px-2 py-1.5",
          COLOR[categoria],
        )}
      >
        <div className="flex items-baseline gap-1.5">
          <span
            className="shrink-0 font-mono text-[10px] opacity-60"
            title="orden de ejecucion"
          >
            {orden}
          </span>
          <span className="font-mono text-xs font-medium">{paso.op}</span>
          <span className="truncate font-mono text-[10px] opacity-75">
            {paso.structure}
          </span>
          {paso.column && (
            <span className="truncate font-mono text-[10px] opacity-60">
              {paso.column}
            </span>
          )}
          <span className="ml-auto shrink-0 font-mono text-[10px] opacity-75">
            {paginas} pag · {paso.time_ms} ms
          </span>
        </div>

        {/* La barra pone el costo a la vista: en un scan ocupa todo el ancho
            y en una busqueda por indice es una marca. */}
        {paginas > 0 && (
          <div className="mt-1 h-0.5 w-full overflow-hidden rounded-full bg-black/10 dark:bg-white/10">
            <div
              className={cn("h-full rounded-full", BARRA[categoria])}
              style={{ width: `${Math.max(porcentaje, 2)}%` }}
            />
          </div>
        )}

        {paso.detail && (
          <p className="mt-1 truncate text-[10px] opacity-70">{paso.detail}</p>
        )}
      </div>
    </li>
  );
}

function Leyenda({ usadas }: { usadas: Categoria[] }) {
  return (
    <div className="flex flex-wrap gap-x-3 gap-y-1 border-t px-3 pt-2">
      {usadas.map((categoria) => (
        <span key={categoria} className="flex items-center gap-1">
          <span
            className={cn("size-2 rounded-full", BARRA[categoria])}
            aria-hidden
          />
          <span className="text-[10px] text-muted-foreground">
            {ETIQUETA[categoria]}
          </span>
        </span>
      ))}
    </div>
  );
}

/** Panel de Plan de Ejecucion (2.1.5, issue #37). */
export function PlanPanel({ plan }: PlanPanelProps) {
  if (!plan) {
    return (
      <Panel titulo="Plan de ejecucion">
        <p className="p-3 text-xs text-muted-foreground">
          El plan aparece al ejecutar una consulta.
        </p>
      </Panel>
    );
  }

  const filas = aplanar(plan.root);
  const maximoPaginas = Math.max(
    ...filas.map((f) => f.paso.stats.pages_read),
    1,
  );
  const usadas = [...new Set(filas.map((f) => CATEGORIA[f.paso.op]))];

  return (
    <Panel
      titulo="Plan de ejecucion"
      nota={`${plan.time_ms} ms · ${plan.totals.pages_read} paginas`}
    >
      <div className="flex h-full min-h-0 flex-col">
        <ol className="min-h-0 flex-1 overflow-auto p-2">
          {filas.map((nodo, indice) => (
            <Fila key={indice} nodo={nodo} maximoPaginas={maximoPaginas} />
          ))}
        </ol>

        <div className="shrink-0">
          <Leyenda usadas={usadas} />
          <p className="px-3 pt-1 pb-2 text-[10px] text-muted-foreground">
            Los numeros son el orden de ejecucion: los hijos corren antes que el
            padre.
          </p>
        </div>
      </div>
    </Panel>
  );
}
