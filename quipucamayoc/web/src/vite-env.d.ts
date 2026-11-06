/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_SERVER_URL?: string;
  /** "true" para trabajar la interfaz con los datos falsos de src/api/mock.ts. */
  readonly VITE_USE_MOCK?: string;
}
