import { useEffect, useState } from "react";
import { health } from "./api.ts";
import Subida from "./Subida.tsx";

export default function App() {
  const [connected, setConnected] = useState<boolean | null>(null);
  const [permiso, setPermiso] = useState(false);
  useEffect(() => {
    let active = true;
    health(import.meta.env.VITE_RIKUQ_API_URL || "/api")
      .then(ok => { if (active) setConnected(ok); })
      .catch(() => { if (active) setConnected(false); });
    return () => { active = false; };
  }, []);
  return <main className="workspace">
    <header><span className="wordmark">rikuq<span> / </span></span><span className="connection">{connected === null ? "Comprobando servidor…" : connected ? "Servidor disponible" : "Servidor no disponible · puerto 8002"}</span></header>
    <section className="intro"><p className="eyebrow">Laboratorio de identidades</p>
      <h1>Una imagen.<br />Una identidad, si la conocemos.</h1>
      <p>La detección ocurre en el servidor. QuipuDB encuentra los rostros cercanos.</p>
    </section>
    <label className="consent"><input type="checkbox" checked={permiso} onChange={e => setPermiso(e.target.checked)} />Tengo permiso para procesar las imágenes de estas personas con fines académicos.</label>
    <Subida permiso={permiso} />
    <footer>Uso académico · Solo imágenes con consentimiento · Sin servicios externos para reconocer rostros</footer>
  </main>;
}
