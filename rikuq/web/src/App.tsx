import { useEffect, useState } from "react";
import { health } from "./api.ts";
import Subida from "./Subida.tsx";
import Camara from "./Camara.tsx";
import { Icon } from "./UI.tsx";

export default function App() {
  const [connected, setConnected] = useState<boolean | null>(null);
  const [permiso, setPermiso] = useState(false);
  const [modo, setModo] = useState<"imagen" | "camara">("imagen");
  const [intento, setIntento] = useState(0);
  useEffect(() => {
    let active = true;
    setConnected(null);
    health(import.meta.env.VITE_RIKUQ_API_URL || "/api")
      .then(ok => { if (active) setConnected(ok); })
      .catch(() => { if (active) setConnected(false); });
    return () => { active = false; };
  }, [intento]);

  return <div className="app-shell">
    <a className="skip-link" href="#reconocimiento">Saltar al reconocimiento</a>
    <header className="app-header"><div className="header-inner wrap">
      <a href="#reconocimiento" className="brand" aria-label="Rikuq, ir al reconocimiento">
        <span className="brand-mark"><Icon name="scan" /></span><span className="wordmark">rikuq</span>
        <span className="brand-divider" /><span className="brand-description">Reconocimiento facial</span>
      </a>
      <div className="header-tools">
        <span className={`connection ${connected === null ? "checking" : connected ? "connected" : "disconnected"}`} role="status">
          <span className="status-dot" />{connected === null ? "Comprobando Rikuq…" : connected ? "API de Rikuq disponible" : "API de Rikuq sin conexión"}
        </span>
        {connected === false && <button type="button" className="btn btn--secondary button secondary compact" onClick={() => setIntento(i => i + 1)}><Icon name="refresh" />Reintentar conexión</button>}
      </div>
    </div></header>

    <main className="workspace wrap" id="reconocimiento" tabIndex={-1}>
      <section className="intro" aria-labelledby="page-title">
        <div><p className="eyebrow">QuipuDB · Demo académica</p>
          <h1 id="page-title">Reconocimiento facial</h1>
          <p className="intro-copy">Compara una foto o la cámara con las referencias de QuipuDB.</p>
        </div>
        <details className="help-menu"><summary>Cómo funciona <span aria-hidden="true">↗</span></summary>
          <div className="help-content"><h2>De la imagen al resultado</h2>
            <ol><li>Rikuq detecta el rostro y extrae su vector.</li><li>QuipuDB busca referencias cercanas con HNSW.</li><li>El umbral decide una coincidencia o Desconocido.</li></ol>
            <p>Subir una imagen consulta la base; no registra personas nuevas. La conexión del encabezado solo comprueba la API de Rikuq.</p>
          </div>
        </details>
      </section>

      {connected === false && <div className="connection-warning" role="alert"><Icon name="refresh" /><div><strong>Rikuq no está respondiendo.</strong><p>Comprueba el servidor del puerto 8002 y vuelve a intentar la conexión.</p></div></div>}

      <div className={`consent ${permiso ? "is-granted" : ""}`}>
        <Icon name="shield" /><label htmlFor="consentimiento"><input id="consentimiento" type="checkbox" checked={permiso} onChange={e => setPermiso(e.target.checked)} />
          <span><strong>Tengo permiso para procesar estas imágenes.</strong><span>Solo para esta sesión y con fines académicos. Puedes retirarlo en cualquier momento.</span></span>
        </label><span className="consent-state">{permiso ? "Permiso confirmado" : "Confirma para continuar"}</span>
      </div>

      <div className="workspace-toolbar">
        <div className="modes" role="tablist" aria-label="Modo de reconocimiento" onKeyDown={e => {
          const keys = ["ArrowLeft", "ArrowRight", "Home", "End"];
          if (!keys.includes(e.key)) return;
          e.preventDefault();
          const next = e.key === "Home" ? "imagen" : e.key === "End" ? "camara" : modo === "imagen" ? "camara" : "imagen";
          setModo(next);
          e.currentTarget.querySelector<HTMLButtonElement>(`#tab-${next}`)?.focus();
        }}>
          <button type="button" id="tab-imagen" role="tab" aria-selected={modo === "imagen"} aria-controls="recognition-panel" tabIndex={modo === "imagen" ? 0 : -1} onClick={() => setModo("imagen")}><Icon name="image" />Subir imagen</button>
          <button type="button" id="tab-camara" role="tab" aria-selected={modo === "camara"} aria-controls="recognition-panel" tabIndex={modo === "camara" ? 0 : -1} onClick={() => setModo("camara")}><Icon name="camera" />Cámara en vivo</button>
        </div>
        <span className="session-note"><Icon name="shield" />Imágenes de consulta en memoria</span>
      </div>
      <div id="recognition-panel" role="tabpanel" aria-labelledby={`tab-${modo}`}>
        {modo === "imagen" ? <Subida permiso={permiso} /> : <Camara permiso={permiso} />}
      </div>
      <footer className="app-footer"><span>Rikuq <span aria-hidden="true">/</span> QuipuDB</span><p>Uso académico local. Las consultas no añaden personas ni guardan las imágenes.</p><span>YuNet · ArcFace · HNSW</span></footer>
    </main>
  </div>;
}
