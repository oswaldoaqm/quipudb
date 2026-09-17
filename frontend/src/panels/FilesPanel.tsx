import { useState } from "react";

import { Panel } from "@/components/Panel";
import { Badge } from "@/components/ui/badge";
import type { ColumnInfo, IndexInfo, TableInfo } from "@/api/types";
import { cn } from "@/lib/utils";

interface FilesPanelProps {
  tablas: TableInfo[];
  cargando: boolean;
}

/** VARCHAR es el unico que declara tamano; el resto lo tiene fijo por tipo. */
function tipoDe(columna: ColumnInfo): string {
  return columna.size === null
    ? columna.type
    : `${columna.type}(${columna.size})`;
}

function Columna({ columna }: { columna: ColumnInfo }) {
  return (
    <li className="flex items-baseline justify-between gap-2 px-3 py-1">
      <span className="flex min-w-0 items-baseline gap-1.5">
        <span className="truncate font-mono text-xs">{columna.name}</span>
        {columna.is_primary_key && (
          <Badge variant="secondary" className="shrink-0 px-1 py-0 text-[9px]">
            PK
          </Badge>
        )}
      </span>
      <span className="shrink-0 font-mono text-[11px] text-muted-foreground">
        {tipoDe(columna)}
      </span>
    </li>
  );
}

function Indice({ indice }: { indice: IndexInfo }) {
  return (
    <li className="px-3 py-1.5">
      <div className="truncate font-mono text-xs">{indice.name}</div>
      <div className="mt-0.5 flex flex-wrap items-center gap-1">
        <Badge variant="outline" className="px-1 py-0 font-mono text-[9px]">
          {indice.structure}
        </Badge>
        <span className="font-mono text-[10px] text-muted-foreground">
          {indice.column}
        </span>
        {/* El hash no puede recorrer un intervalo: lo dice el propio contrato
            del core con supports_range (ADR 0002, regla 2). */}
        <span className="text-[10px] text-muted-foreground">
          {indice.supports_range ? "· igualdad y rango" : "· solo igualdad"}
        </span>
      </div>
    </li>
  );
}

function Detalle({ tabla }: { tabla: TableInfo }) {
  return (
    <div className="pb-2">
      <div className="px-3 py-2">
        <div className="truncate font-mono text-sm font-medium">
          {tabla.name}
        </div>
        <div className="mt-1 flex flex-wrap items-center gap-1">
          <Badge variant="outline" className="px-1 py-0 font-mono text-[9px]">
            {tabla.storage}
          </Badge>
          <span className="text-[10px] text-muted-foreground">
            {tabla.record_count.toLocaleString("es-PE")} registros
          </span>
        </div>
      </div>

      <h3 className="px-3 pt-1 pb-0.5 text-[10px] tracking-wide text-muted-foreground uppercase">
        Columnas
      </h3>
      <ul>
        {tabla.columns.map((columna) => (
          <Columna key={columna.name} columna={columna} />
        ))}
      </ul>

      <h3 className="px-3 pt-2 pb-0.5 text-[10px] tracking-wide text-muted-foreground uppercase">
        Indices
      </h3>
      {tabla.indexes.length === 0 ? (
        <p className="px-3 py-1 text-[11px] text-muted-foreground">
          Sin indices secundarios.
        </p>
      ) : (
        <ul>
          {tabla.indexes.map((indice) => (
            <Indice key={indice.name} indice={indice} />
          ))}
        </ul>
      )}
    </div>
  );
}

/** Panel de Archivos (2.1.5, issue #34): que tablas hay y como estan hechas. */
export function FilesPanel({ tablas, cargando }: FilesPanelProps) {
  const [elegida, setElegida] = useState<string | null>(null);

  // Caer a la primera evita un efecto para la seleccion inicial, y sostiene el
  // caso de que la tabla elegida desaparezca del catalogo al recargarlo.
  const tabla = tablas.find((t) => t.name === elegida) ?? tablas[0] ?? null;

  return (
    <Panel titulo="Archivos" nota={`${tablas.length} tablas`}>
      {cargando ? (
        <p className="p-3 text-xs text-muted-foreground">Cargando catalogo...</p>
      ) : tabla === null ? (
        <p className="p-3 text-xs text-muted-foreground">
          No hay tablas en el catalogo.
        </p>
      ) : (
        <div className="flex h-full min-h-0 flex-col">
          {/* El tope de altura es lo que impide que un catalogo largo empuje
              el detalle fuera del panel. */}
          <ul className="max-h-[45%] shrink-0 overflow-auto border-b">
            {tablas.map((fila) => (
              <li key={fila.name}>
                <button
                  type="button"
                  onClick={() => setElegida(fila.name)}
                  aria-current={fila.name === tabla.name}
                  className={cn(
                    "flex w-full items-baseline justify-between gap-2 px-3 py-1.5 text-left hover:bg-muted/60",
                    fila.name === tabla.name && "bg-muted",
                  )}
                >
                  <span className="truncate font-mono text-xs">{fila.name}</span>
                  <span className="shrink-0 font-mono text-[10px] text-muted-foreground">
                    {fila.storage}
                  </span>
                </button>
              </li>
            ))}
          </ul>

          <div className="min-h-0 flex-1 overflow-auto">
            <Detalle tabla={tabla} />
          </div>
        </div>
      )}
    </Panel>
  );
}
