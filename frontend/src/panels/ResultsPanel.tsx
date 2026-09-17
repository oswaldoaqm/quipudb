import { useVirtualizer } from "@tanstack/react-virtual";
import { useRef } from "react";

import { Panel } from "@/components/Panel";
import type { CellValue, QueryResult } from "@/api/types";
import { cn } from "@/lib/utils";

interface ResultsPanelProps {
  resultado: QueryResult | null;
  /** El detalle del error vive en el panel de consultas, junto al editor. */
  hayError: boolean;
  ejecutando: boolean;
}

const ALTO_FILA = 26;
const ANCHO_COLUMNA = 110;

function formatear(valor: CellValue): string {
  if (typeof valor === "boolean") return valor ? "true" : "false";
  return String(valor);
}

/** Los numeros se leen mejor alineados a la derecha, como en cualquier hoja. */
function esNumerico(tipo: string | undefined): boolean {
  return tipo === "INT" || tipo === "DOUBLE";
}

function Tabla({ resultado }: { resultado: QueryResult }) {
  const contenedorRef = useRef<HTMLDivElement>(null);

  // Solo se montan las filas visibles mas un margen. Sin esto, 10 000 filas
  // son 30 000 celdas en el DOM y la pestana se congela al desplazarse.
  const virtual = useVirtualizer({
    count: resultado.rows.length,
    getScrollElement: () => contenedorRef.current,
    estimateSize: () => ALTO_FILA,
    overscan: 12,
  });

  const plantilla = `repeat(${resultado.columns.length}, minmax(${ANCHO_COLUMNA}px, 1fr))`;
  const anchoMinimo = resultado.columns.length * ANCHO_COLUMNA;

  return (
    <div ref={contenedorRef} className="h-full overflow-auto" role="table">
      <div
        className="sticky top-0 z-10 grid border-b bg-muted"
        style={{ gridTemplateColumns: plantilla, minWidth: anchoMinimo }}
        role="row"
      >
        {resultado.columns.map((columna, indice) => (
          <div key={columna} className="px-3 py-1" role="columnheader">
            <div className="truncate font-mono text-xs font-medium">
              {columna}
            </div>
            <div className="truncate font-mono text-[10px] text-muted-foreground">
              {resultado.column_types[indice] ?? "?"}
            </div>
          </div>
        ))}
      </div>

      <div
        style={{
          height: virtual.getTotalSize(),
          minWidth: anchoMinimo,
          position: "relative",
        }}
      >
        {virtual.getVirtualItems().map((item) => {
          const fila = resultado.rows[item.index];
          if (!fila) return null;
          return (
            <div
              key={item.key}
              role="row"
              className="absolute top-0 left-0 grid w-full border-b hover:bg-muted/50"
              style={{
                gridTemplateColumns: plantilla,
                height: item.size,
                transform: `translateY(${item.start}px)`,
              }}
            >
              {fila.map((celda, indiceCelda) => (
                <div
                  key={indiceCelda}
                  role="cell"
                  className={cn(
                    "truncate px-3 py-1 font-mono text-xs",
                    esNumerico(resultado.column_types[indiceCelda]) &&
                      "text-right",
                  )}
                >
                  {celda === null ? (
                    <span className="text-muted-foreground">NULL</span>
                  ) : (
                    formatear(celda)
                  )}
                </div>
              ))}
            </div>
          );
        })}
      </div>
    </div>
  );
}

/** Panel de Resultados (2.1.5, issue #36): las filas que devolvio la consulta. */
export function ResultsPanel({
  resultado,
  hayError,
  ejecutando,
}: ResultsPanelProps) {
  // El tiempo lo mide el planner de parsear a devolver (ADR 0002), asi que es
  // el que el usuario percibe y el que corresponde mostrar aqui.
  const nota =
    resultado && !hayError
      ? [
          `${resultado.rows.length.toLocaleString("es-PE")} filas`,
          resultado.plan && `${resultado.plan.time_ms} ms`,
        ]
          .filter(Boolean)
          .join(" · ")
      : undefined;

  return (
    <Panel titulo="Resultados" nota={nota}>
      {hayError ? (
        <p className="p-3 text-xs text-muted-foreground">
          La consulta no se ejecuto. El detalle esta junto al editor.
        </p>
      ) : ejecutando ? (
        <p className="p-3 text-xs text-muted-foreground">Ejecutando...</p>
      ) : !resultado ? (
        <p className="p-3 text-xs text-muted-foreground">
          Ejecuta una consulta para ver resultados.
        </p>
      ) : resultado.columns.length === 0 ? (
        <p className="p-3 text-xs text-muted-foreground">
          {resultado.affected_rows} filas afectadas.
        </p>
      ) : resultado.rows.length === 0 ? (
        <p className="p-3 text-xs text-muted-foreground">
          La consulta no devolvio filas.
        </p>
      ) : (
        <Tabla resultado={resultado} />
      )}
    </Panel>
  );
}
