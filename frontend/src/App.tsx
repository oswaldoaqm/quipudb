import { useCallback, useEffect, useReducer, useRef, useState } from "react";

import {
  executeQuery,
  listTables,
  loadCsv,
  USA_DATOS_FALSOS,
} from "@/api/client";
import { MotorError } from "@/api/errors";
import { CONSULTA_DE_PRUEBA } from "@/api/mock";
import { efectoDe, type Efecto } from "@/api/sentencia";
import type { TableInfo } from "@/api/types";
import { useSpatialContext } from "@/hooks/useSpatialContext";
import { executionReducer, initialExecution } from "@/lib/query-execution";
import { FilesPanel } from "@/panels/FilesPanel";
import { MapPanel } from "@/panels/MapPanel";
import { PlanPanel } from "@/panels/PlanPanel";
import { QueryPanel } from "@/panels/QueryPanel";
import { ResultsPanel } from "@/panels/ResultsPanel";

export default function App() {
  const [tablas, setTablas] = useState<TableInfo[]>([]);
  const [cargandoTablas, setCargandoTablas] = useState(true);

  const [sql, setSql] = useState(CONSULTA_DE_PRUEBA);
  const [execution, dispatch] = useReducer(executionReducer, initialExecution);
  const requestId = useRef(0);
  const { result: resultado, error, pending: ejecutando } = execution;
  const [baseRevision, setBaseRevision] = useState(0);
  const base = useSpatialContext(resultado, tablas, baseRevision);
  const [efecto, setEfecto] = useState<Efecto>({ clase: "otra" });

  const recargarCatalogo = useCallback(
    () => listTables().then(setTablas).catch(() => setTablas([])),
    [],
  );

  const ejecutar = useCallback(
    async (sentencia: string) => {
      // Tambien por el atajo: el boton ya esta deshabilitado, pero Ctrl+Enter
      // lo saltaria y el motor respondería "0 filas afectadas", que no
      // significa nada.
      if (sentencia.trim() === "") return;

      const id = ++requestId.current;
      dispatch({ type: "begin", id, sql: sentencia });
      try {
        const result = await executeQuery(sentencia);
        const efectoNuevo = efectoDe(sentencia);
        if (result.affected_rows > 0 || efectoNuevo.clase !== "otra" || !result.plan) {
          setBaseRevision(revision => revision + 1);
        }
        if (id !== requestId.current) return;
        // Tambien en lotes cuya ultima sentencia es SELECT: la tabla puede ser nueva.
        const catalogo = await listTables().catch(() => [] as TableInfo[]);
        if (id !== requestId.current) return;
        setTablas(catalogo);
        setEfecto(efectoNuevo);
        dispatch({ type: "complete", id, result });
      } catch (fallo) {
        // Un lote puede haber escrito antes del error; no reutilizar esa cache.
        setBaseRevision(revision => revision + 1);
        if (id !== requestId.current) return;
        dispatch({
          type: "fail", id,
          error: fallo instanceof MotorError ? fallo : MotorError.sinUbicacion(
            fallo instanceof Error ? fallo.message : String(fallo),
          ),
        });
        setEfecto({ clase: "otra" });
      }
    },
    [],
  );

  const cargarCsv = useCallback(
    async (tabla: string, archivo: File) => {
      try {
        return await loadCsv(tabla, archivo);
      } finally {
        setBaseRevision(revision => revision + 1);
        await recargarCatalogo();
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

      <main className="grid min-h-0 flex-1 grid-cols-1 grid-rows-[minmax(130px,22%)_1fr] gap-2 lg:grid-cols-[240px_minmax(0,1fr)] lg:grid-rows-1">
        <FilesPanel
          tablas={tablas}
          cargando={cargandoTablas}
          onCargarCsv={cargarCsv}
        />

        <div className="grid min-h-0 min-w-0 grid-rows-[minmax(160px,30%)_1fr] gap-2">
          <QueryPanel
            sql={sql}
            onSqlChange={setSql}
            onEjecutar={() => void ejecutar(sql)}
            ejecutando={ejecutando}
            error={error}
          />

          <div className="grid min-h-0 auto-rows-[minmax(320px,1fr)] grid-cols-1 gap-2 overflow-auto xl:grid-cols-2">
            <ResultsPanel
              resultado={resultado}
              hayError={error !== null}
              ejecutando={ejecutando}
              efecto={efecto}
            />
            <MapPanel
              resultado={resultado}
              hayError={error !== null}
              ejecutando={ejecutando}
              base={base}
            />
            <div className="grid min-h-0 xl:col-span-2">
              <PlanPanel plan={resultado?.plan ?? null} />
            </div>
          </div>
        </div>
      </main>
    </div>
  );
}
