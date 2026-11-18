import { useEffect, useRef, useState } from "react";
import { ApiError, identificar, mensajeError, validarImagen, type Identificacion } from "./api.ts";
import { Cajas, Resultado } from "./Resultado.tsx";
import { Icon, ScanFrame } from "./UI.tsx";

export default function Subida({ permiso }: { permiso: boolean }) {
  const [archivo, setArchivo] = useState<File | null>(null);
  const [preview, setPreview] = useState("");
  const [resultado, setResultado] = useState<Identificacion | null>(null);
  const [estado, setEstado] = useState("listo");
  const [error, setError] = useState("");
  const [drag, setDrag] = useState(false);
  const peticion = useRef<AbortController | null>(null);
  const selector = useRef<HTMLInputElement | null>(null);
  useEffect(() => () => { peticion.current?.abort(); peticion.current = null; }, []);
  useEffect(() => {
    if (!permiso) { peticion.current?.abort(); peticion.current = null; setEstado("listo"); setResultado(null); }
  }, [permiso]);
  useEffect(() => {
    if (!archivo) { setPreview(""); return; }
    const url = URL.createObjectURL(archivo);
    setPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [archivo]);

  function elegir(files: FileList | null) {
    peticion.current?.abort();
    peticion.current = null;
    setResultado(null);
    setEstado("listo");
    setError("");
    if (!files?.length) return;
    if (files.length > 1) { setError("Elige una sola imagen por consulta."); setArchivo(null); return; }
    const mensaje = validarImagen(files[0]);
    if (mensaje) { setError(mensaje); setArchivo(null); return; }
    setArchivo(files[0]);
  }

  async function reconocer() {
    if (!archivo || !permiso || estado === "subiendo") return;
    const controller = new AbortController();
    peticion.current = controller;
    const timeout = setTimeout(() => controller.abort(new DOMException("Tiempo agotado", "TimeoutError")), 90_000);
    setEstado("subiendo"); setResultado(null); setError("");
    try {
      const response = await identificar(archivo, controller.signal);
      if (peticion.current === controller) { setResultado(response); setEstado("resultado"); }
    } catch (e) {
      if (peticion.current !== controller) return;
      setEstado(e instanceof ApiError && e.codigo === "sin_rostro" ? "sin_rostro" : "error");
      setError(controller.signal.aborted ? "La consulta agotó el tiempo de espera. Revisa el servidor." : mensajeError(e));
    } finally { clearTimeout(timeout); }
  }

  function cancelar(quitar = false) {
    peticion.current?.abort(); peticion.current = null;
    setEstado("listo"); setResultado(null); setError("");
    if (quitar) setArchivo(null);
  }

  return <div className="recognition-grid">
    <section className="input-panel" aria-labelledby="input-title">
      <div className="panel-heading"><div><span className="section-label">Imagen de consulta</span><h2 id="input-title">{archivo ? "Tu imagen de consulta" : "Elige una foto para empezar"}</h2></div><span className="format-tag">Hasta 8 MiB</span></div>
      <div className={`dropzone ${drag ? "dragging" : ""}`} onDragOver={e => { e.preventDefault(); setDrag(true); }}
        onDragLeave={() => setDrag(false)} onDrop={e => { e.preventDefault(); setDrag(false); elegir(e.dataTransfer.files); }}>
        <div className={`stage ${preview ? "" : "is-empty"}`}>
          {preview ? <img src={preview} alt="Imagen seleccionada para identificar" /> : <div className="stage-empty"><ScanFrame /><p className="stage-title">Tu foto va aquí</p><p>Arrastra una imagen o selecciónala desde tu equipo.</p><button type="button" className="btn btn--secondary button secondary" onClick={() => selector.current?.click()}><Icon name="image" />Elegir imagen</button><span className="file-types">JPEG, PNG o WebP</span></div>}
          {resultado && <Cajas resultado={resultado} />}
          {estado === "subiendo" && <div className="analysis-status" role="status"><Icon name="scan" />Analizando imagen…</div>}
        </div>
        <div className="file-bar"><span className="file-name"><Icon name="image" /><span>{archivo?.name || "Ninguna imagen seleccionada"}</span></span>
          {archivo && <div className="file-actions"><button type="button" className="btn btn--secondary button secondary compact" onClick={() => selector.current?.click()}>Cambiar imagen</button><button type="button" className="btn btn--secondary button secondary icon-button" aria-label="Quitar imagen" onClick={() => cancelar(true)}><Icon name="close" /></button></div>}
          <input ref={selector} id="imagen" hidden aria-label="Seleccionar imagen para identificar" type="file" accept="image/jpeg,image/png,image/webp" onChange={e => { elegir(e.target.files); e.target.value = ""; }} />
        </div>
      </div>
      <div className="analysis-actions"><button type="button" className="btn btn--primary button primary" aria-describedby="upload-guidance" disabled={!archivo || !permiso || estado === "subiendo"} onClick={reconocer}>
        <Icon name={estado === "subiendo" ? "scan" : "arrow"} />{estado === "subiendo" ? "Analizando imagen…" : "Identificar rostro"}
      </button>{estado === "subiendo" && <button type="button" className="btn btn--secondary button secondary" onClick={() => cancelar()}>Cancelar análisis</button>}</div>
      <p id="upload-guidance" className="action-guidance">{!permiso ? "Confirma el permiso de arriba para analizar la imagen." : !archivo ? "Selecciona una imagen para activar el análisis." : estado === "subiendo" ? "Puedes cancelar la consulta o elegir otra imagen." : "Imagen lista. Pulsa Identificar rostro para comparar."}</p>
      {error && <div className="error" role="alert"><strong>{estado === "sin_rostro" ? "No encontramos un rostro" : "No se pudo analizar la imagen"}</strong><p>{error}</p></div>}
      <div className="input-tip"><Icon name="shield" /><p>Elige una foto con el rostro visible y buena iluminación. La consulta no registra personas nuevas.</p></div>
    </section>
    <Resultado resultado={resultado} analizando={estado === "subiendo"} />
  </div>;
}
