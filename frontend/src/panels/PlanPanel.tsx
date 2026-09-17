import { Panel } from "@/components/Panel";
import type { Plan, Step } from "@/api/types";

interface PlanPanelProps {
  plan: Plan | null;
}

/** El paso no toco disco: no tiene estructura que resaltar (ADR 0002). */
function esEnMemoria(paso: Step): boolean {
  return paso.structure === "memory";
}

function Nodo({ paso, nivel }: { paso: Step; nivel: number }) {
  return (
    <li>
      <div
        className="flex items-baseline gap-2 py-1"
        style={{ paddingLeft: `${nivel * 16}px` }}
      >
        <span className="font-mono text-xs font-medium">{paso.op}</span>
        <span
          className={
            esEnMemoria(paso)
              ? "rounded bg-muted px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground"
              : "rounded bg-accent px-1.5 py-0.5 font-mono text-[10px] text-accent-foreground"
          }
        >
          {paso.structure}
        </span>
        {paso.table ? (
          <span className="font-mono text-[11px] text-muted-foreground">
            {paso.table}
          </span>
        ) : null}
        <span className="ml-auto shrink-0 font-mono text-[10px] text-muted-foreground">
          {paso.stats.pages_read} pag · {paso.time_ms} ms
        </span>
      </div>

      {paso.detail ? (
        <p
          className="pb-1 text-[11px] text-muted-foreground"
          style={{ paddingLeft: `${nivel * 16 + 8}px` }}
        >
          {paso.detail}
        </p>
      ) : null}

      {paso.children.length > 0 && (
        <ul>
          {paso.children.map((hijo, indice) => (
            <Nodo key={indice} paso={hijo} nivel={nivel + 1} />
          ))}
        </ul>
      )}
    </li>
  );
}

/** Panel de Plan de Ejecucion (2.1.5). La vista de grafo es el issue #37. */
export function PlanPanel({ plan }: PlanPanelProps) {
  return (
    <Panel
      titulo="Plan de ejecucion"
      nota={plan ? `${plan.time_ms} ms · ${plan.totals.pages_read} paginas` : undefined}
    >
      {!plan ? (
        <p className="p-3 text-xs text-muted-foreground">
          El plan aparece al ejecutar una consulta.
        </p>
      ) : (
        <div className="p-2">
          <ul>
            <Nodo paso={plan.root} nivel={0} />
          </ul>
          <p className="mt-2 border-t px-1 pt-2 text-[10px] text-muted-foreground">
            Los hijos se ejecutan antes que el padre: se lee de abajo hacia arriba.
          </p>
        </div>
      )}
    </Panel>
  );
}
