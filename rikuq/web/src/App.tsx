import { useEffect, useState } from "react";
import { health } from "./api.ts";

export default function App() {
  const [connected, setConnected] = useState<boolean | null>(null);
  useEffect(() => {
    let active = true;
    health(import.meta.env.VITE_RIKUQ_API_URL || "/api")
      .then(ok => { if (active) setConnected(ok); })
      .catch(() => { if (active) setConnected(false); });
    return () => { active = false; };
  }, []);
  return <main className="workspace">
    <header><span className="wordmark">rikuq<span> / </span></span><span>Reconocimiento facial · QuipuDB</span></header>
    <section className="intro"><p className="eyebrow">Laboratorio de identidades</p>
      <h1>Una imagen.<br />Una identidad, si la conocemos.</h1>
      <p>La detección ocurre en el servidor. QuipuDB encuentra los rostros cercanos.</p>
    </section>
    <section className="placeholder" aria-label="Estado del servidor">
      <span className="frame-mark" aria-hidden="true">⌜ ⌝<br />⌞ ⌟</span>
      <h2>El espacio de reconocimiento está listo</h2>
      <p role="status">{connected === null ? "Comprobando servidor…" : connected ? "Servidor de Rikuq disponible" : "Levanta el servidor de Rikuq en el puerto 8002."}</p>
    </section>
    <footer>Uso académico · Solo imágenes con consentimiento · Sin servicios externos para reconocer rostros</footer>
  </main>;
}
