import { Panel } from "@/components/Panel";
import type { CellValue, QueryResult } from "@/api/types";

interface ResultsPanelProps {
  resultado: QueryResult | null;
  error: string | null;
  ejecutando: boolean;
}

function formatear(valor: CellValue): string {
  if (valor === null) return "NULL";
  if (typeof valor === "boolean") return valor ? "true" : "false";
  return String(valor);
}

/** Panel de Resultados (2.1.5). La tabla con orden y paginado es el issue #36. */
export function ResultsPanel({
  resultado,
  error,
  ejecutando,
}: ResultsPanelProps) {
  const nota = resultado ? `${resultado.rows.length} filas` : undefined;

  return (
    <Panel titulo="Resultados" nota={nota}>
      {error ? (
        <p className="p-3 font-mono text-xs text-destructive">{error}</p>
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
      ) : (
        <table className="w-full border-collapse text-left text-xs">
          <thead className="sticky top-0 bg-muted">
            <tr>
              {resultado.columns.map((columna) => (
                <th
                  key={columna}
                  className="border-b px-3 py-1.5 font-mono font-medium whitespace-nowrap"
                >
                  {columna}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {resultado.rows.map((fila, indiceFila) => (
              <tr key={indiceFila} className="hover:bg-muted/50">
                {fila.map((celda, indiceCelda) => (
                  <td
                    key={indiceCelda}
                    className="border-b px-3 py-1 font-mono whitespace-nowrap"
                  >
                    {celda === null ? (
                      <span className="text-muted-foreground">NULL</span>
                    ) : (
                      formatear(celda)
                    )}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Panel>
  );
}
