import { useEffect, useState } from "react";
import { health } from "./api.ts";
import Subida from "./Subida.tsx";
import Camara from "./Camara.tsx";
import Registro from "./Registro.tsx";
import Registradas from "./Registradas.tsx";
import { Icon, type IconName } from "./UI.tsx";

type Modo = "imagen" | "camara" | "registrar" | "registradas";
const MODOS: { id: Modo; icono: IconName; texto: string }[] = [
  { id: "imagen", icono: "image", texto: "Subir imagen" },
  { id: "camara", icono: "camera", texto: "Cámara en vivo" },
  { id: "registrar", icono: "plus", texto: "Registrar" },
  { id: "registradas", icono: "users", texto: "Fotos registradas" },
];

export default function App() {
  const [connected, setConnected] = useState<boolean | null>(null);
  const [permiso, setPermiso] = useState(false);
  const [modo, setModo] = useState<Modo>("imagen");
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
            <p>Subir una imagen o usar la cámara solo consulta la base. Para añadir a alguien está Registrar, y sus fotos se pueden retirar en Fotos registradas. La conexión del encabezado solo comprueba la API de Rikuq.</p>
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
          const actual = MODOS.findIndex(m => m.id === modo);
          const indice = e.key === "Home" ? 0 : e.key === "End" ? MODOS.length - 1
            : (actual + (e.key === "ArrowRight" ? 1 : -1) + MODOS.length) % MODOS.length;
          const next = MODOS[indice].id;
          setModo(next);
          e.currentTarget.querySelector<HTMLButtonElement>(`#tab-${next}`)?.focus();
        }}>
          {MODOS.map(m => <button key={m.id} type="button" id={`tab-${m.id}`} role="tab" aria-selected={modo === m.id} aria-controls="recognition-panel" tabIndex={modo === m.id ? 0 : -1} onClick={() => setModo(m.id)}><Icon name={m.icono} />{m.texto}</button>)}
        </div>
        <span className="session-note"><Icon name="shield" />{modo === "registrar" || modo === "registradas" ? "Solo Registrar guarda fotos" : "Imágenes de consulta en memoria"}</span>
      </div>
      <div id="recognition-panel" role="tabpanel" aria-labelledby={`tab-${modo}`}>
        {modo === "imagen" ? <Subida permiso={permiso} />
          : modo === "camara" ? <Camara permiso={permiso} />
            : modo === "registrar" ? <Registro permiso={permiso} /> : <Registradas />}
      </div>
      <footer className="app-footer"><span>Rikuq <span aria-hidden="true">/</span> QuipuDB</span><p>Uso académico local. Las consultas no guardan imágenes; solo Registrar añade fotos a la base, y se pueden eliminar.</p><span>YuNet · ArcFace · HNSW</span></footer>
    </main>
  </div>;
}
