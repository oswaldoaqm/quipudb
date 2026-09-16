import { Panel } from "@/components/Panel";
import type { TableInfo } from "@/api/types";

interface FilesPanelProps {
  tablas: TableInfo[];
  cargando: boolean;
}

/** Panel de Archivos (2.1.5). El detalle de la estructura es el issue #34. */
export function FilesPanel({ tablas, cargando }: FilesPanelProps) {
  return (
    <Panel titulo="Archivos" nota={`${tablas.length} tablas`}>
      {cargando ? (
        <p className="p-3 text-xs text-muted-foreground">Cargando catalogo...</p>
      ) : (
        <ul className="divide-y">
          {tablas.map((tabla) => (
            <li key={tabla.name} className="px-3 py-2">
              <div className="flex items-baseline justify-between gap-2">
                <span className="font-mono text-sm">{tabla.name}</span>
                <span className="font-mono text-[11px] text-muted-foreground">
                  {tabla.storage}
                </span>
              </div>

              <ul className="mt-1 space-y-0.5">
                {tabla.columns.map((columna) => (
                  <li
                    key={columna.name}
                    className="flex items-baseline justify-between gap-2 text-xs"
                  >
                    <span className="truncate text-muted-foreground">
                      {columna.is_primary_key ? "PK " : ""}
                      {columna.name}
                    </span>
                    <span className="shrink-0 font-mono text-[11px] text-muted-foreground">
                      {columna.type}
                      {columna.size === null ? "" : `(${columna.size})`}
                    </span>
                  </li>
                ))}
              </ul>

              {tabla.indexes.length > 0 && (
                <ul className="mt-1.5 space-y-0.5 border-t pt-1.5">
                  {tabla.indexes.map((indice) => (
                    <li
                      key={indice.name}
                      className="flex items-baseline justify-between gap-2 text-[11px]"
                    >
                      <span className="truncate text-muted-foreground">
                        {indice.name}
                      </span>
                      <span className="shrink-0 font-mono text-muted-foreground">
                        {indice.structure}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}
