/** Espejo de los modelos de quipucamayoc/server/app/main.py. */

export interface Health {
  status: "ok" | "degraded";
  engine: {
    url: string;
    reachable: boolean;
    tables: number | null;
    error: string | null;
  };
  llm: {
    provider: string;
    model: string;
    configured: boolean;
  };
}
