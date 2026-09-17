/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Origen de la API del motor. Por defecto http://localhost:8000 */
  readonly VITE_API_URL?: string;
  /** "false" fuerza el uso de la API real; cualquier otro valor usa datos falsos. */
  readonly VITE_USE_MOCK?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
