import { useCallback, useEffect, useState } from "react";

import {
  executeQuery,
  listTables,
  loadCsv,
  USA_DATOS_FALSOS,
} from "@/api/client";
import { MotorError } from "@/api/errors";
import { CONSULTA_DE_PRUEBA } from "@/api/mock";
import { efectoDe, type Efecto } from "@/api/sentencia";
import type { QueryResult, TableInfo } from "@/api/types";
import { FilesPanel } from "@/panels/FilesPanel";
import { MapPanel } from "@/panels/MapPanel";
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

      setEjecutando(true);
      setError(null);
      try {
        setResultado(await executeQuery(sentencia));
        const efectoNuevo = efectoDe(sentencia);
        setEfecto(efectoNuevo);
        // Una tabla nueva o filas nuevas cambian lo que muestra el Panel de
        // Archivos, y tiene que verse sin recargar la pagina (criterio 3
        // del #111).
        if (efectoNuevo.clase !== "otra") await recargarCatalogo();
      } catch (fallo) {
        setError(
          fallo instanceof MotorError
            ? fallo
            : MotorError.sinUbicacion(
                fallo instanceof Error ? fallo.message : String(fallo),
              ),
        );
        setResultado(null);
        setEfecto({ clase: "otra" });
      } finally {
        setEjecutando(false);
      }
    },
    [recargarCatalogo],
  );

  const cargarCsv = useCallback(
    async (tabla: string, archivo: File) => {
      const informe = await loadCsv(tabla, archivo);
      // El conteo de registros de la tabla cambio, y el Panel de Archivos lo
      // muestra: hay que releerlo aunque la carga haya fallado a medias.
      await recargarCatalogo();
      return informe;
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
