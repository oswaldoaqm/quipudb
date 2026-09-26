import { useCallback, useEffect, useState } from "react";

import { executeQuery, listTables, USA_DATOS_FALSOS } from "@/api/client";
import { MotorError } from "@/api/errors";
import { CONSULTA_DE_PRUEBA } from "@/api/mock";
import { tablaCreadaPor } from "@/api/sentencia";
import type { QueryResult, TableInfo } from "@/api/types";
import { FilesPanel } from "@/panels/FilesPanel";
import { PlanPanel } from "@/panels/PlanPanel";
import { QueryPanel } from "@/panels/QueryPanel";
import { ResultsPanel } from "@/panels/ResultsPanel";

export default function App() {
  const [tablas, setTablas] = useState<TableInfo[]>([]);
  const [cargandoTablas, setCargandoTablas] = useState(true);

  const [sql, setSql] = useState(CONSULTA_DE_PRUEBA);
  const [resultado, setResultado] = useState<QueryResult | null>(null);
  const [error, setError] = useState<MotorError | null>(null);
  const [ejecutando, setEjecutando] = useState(false);
  const [tablaCreada, setTablaCreada] = useState<string | null>(null);

  const recargarCatalogo = useCallback(
    () => listTables().then(setTablas).catch(() => setTablas([])),
    [],
  );

  const ejecutar = useCallback(
    async (sentencia: string) => {
      setEjecutando(true);
      setError(null);
      try {
        setResultado(await executeQuery(sentencia));
        const creada = tablaCreadaPor(sentencia);
        setTablaCreada(creada);
        // Criterio 3 del #111: la tabla nueva tiene que aparecer en el Panel
        // de Archivos sin que haya que recargar la pagina.
        if (creada) await recargarCatalogo();
      } catch (fallo) {
        setError(
          fallo instanceof MotorError
            ? fallo
            : MotorError.sinUbicacion(
                fallo instanceof Error ? fallo.message : String(fallo),
              ),
        );
        setResultado(null);
        setTablaCreada(null);
      } finally {
        setEjecutando(false);
      }
    },
    [recargarCatalogo],
  );

  useEffect(() => {
    void recargarCatalogo().finally(() => setCargandoTablas(false));

    // La consulta fija de prueba del issue #33: al abrir ya se ve el camino
    // completo, de la interfaz al motor y de vuelta con filas y plan.
    void ejecutar(CONSULTA_DE_PRUEBA);
  }, [ejecutar, recargarCatalogo]);

  return (
    <div className="flex h-screen flex-col gap-2 bg-background p-2 text-foreground">
      <header className="flex shrink-0 items-baseline gap-3 px-1">
        <h1 className="text-sm font-semibold">QuipuDB</h1>
        <span className="text-xs text-muted-foreground">
          Motor de base de datos multimodal
        </span>
        <span className="ml-auto rounded bg-muted px-2 py-0.5 text-[11px] text-muted-foreground">
          {USA_DATOS_FALSOS ? "datos falsos" : "motor conectado"}
        </span>
      </header>

      <main className="grid min-h-0 flex-1 grid-cols-[280px_1fr] gap-2">
        <FilesPanel tablas={tablas} cargando={cargandoTablas} />

        <div className="grid min-h-0 min-w-0 grid-rows-[minmax(140px,32%)_1fr] gap-2">
          <QueryPanel
            sql={sql}
            onSqlChange={setSql}
            onEjecutar={() => void ejecutar(sql)}
            ejecutando={ejecutando}
            error={error}
          />

          <div className="grid min-h-0 flex-1 grid-cols-2 gap-2">
            <ResultsPanel
              resultado={resultado}
              hayError={error !== null}
              ejecutando={ejecutando}
              tablaCreada={tablaCreada}
            />
            <PlanPanel plan={resultado?.plan ?? null} />
          </div>
        </div>
      </main>
    </div>
  );
}
