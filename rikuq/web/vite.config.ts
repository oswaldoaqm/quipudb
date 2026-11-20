import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    // Sin host, Vite escucha en localhost, que con Node 17+ en Windows es solo
    // [::1]: la URL del README (127.0.0.1) quedaba rechazada (#340). Con la IP
    // tambien abre localhost: el navegador prueba IPv4 si IPv6 no responde.
    host: "127.0.0.1",
    port: 5175,
    strictPort: true,
    proxy: { "/api": { target: "http://127.0.0.1:8002", rewrite: p => p.replace(/^\/api/, "") } },
  },
});
