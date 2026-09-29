import { useEffect, useState } from "react";
import { executeQuery } from "@/api/client";
import type { QueryResult, TableInfo } from "@/api/types";
import { baseCacheKey, createSpatialBaseCache } from "@/lib/spatial-context";

export interface SpatialBase {
  result: QueryResult | null;
  table: TableInfo | null;
  message: string | null;
}

/** Los cambios de centro/radio reutilizan la misma tabla; el mapa nunca consulta. */
export function useSpatialContext(
  result: QueryResult | null,
  tables: TableInfo[],
  revision: number,
): SpatialBase {
  const [cache] = useState(() => createSpatialBaseCache(executeQuery));
  const [loaded, setLoaded] = useState<{
    key: string; result: QueryResult | null; message: string | null;
  } | null>(null);
  const source = result?.is_explain ? null : result?.spatial_context?.table;
  const table = tables.find(item => item.name === source) ?? null;
  const key = table ? baseCacheKey(table, revision) : null;

  useEffect(() => {
    if (!table || !key) return;
    let active = true;
    void Promise.resolve().then(() => cache.load(table, revision)).then(
      response => {
        if (active) setLoaded({ key, result: response, message: null });
      },
      () => {
        if (active) setLoaded({ key, result: null, message: "No se pudieron cargar los puntos base. Los resultados siguen disponibles." });
      },
    );
    return () => { active = false; };
  }, [cache, table, key, revision]);

  if (!source) return { table: null, result: null, message: null };
  if (!table) return { table: null, result: null, message: "No hay metadatos de la tabla para cargar los puntos base." };
  // Nunca exponemos datos de la tabla/revision anterior durante un cambio.
  if (loaded?.key !== key) return { table, result: null, message: "Cargando puntos base…" };
  return { table, result: loaded.result, message: loaded.message };
}
