import { useEffect, useRef, useState } from "react";
import { ApiError, identificar, mensajeError, validarImagen, type Identificacion } from "./api.ts";
import { Cajas, Resultado } from "./Resultado.tsx";

export default function Subida({ permiso }: { permiso: boolean }) {
  const [archivo, setArchivo] = useState<File | null>(null);
  const [preview, setPreview] = useState("");
  const [resultado, setResultado] = useState<Identificacion | null>(null);
  const [estado, setEstado] = useState("listo");
  const [error, setError] = useState("");
  const [drag, setDrag] = useState(false);
  const peticion = useRef<AbortController | null>(null);
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
    if (files.length > 1) { setError("Elige una sola imagen por consulta."); return; }
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

  return <div className="recognition-grid">
    <section className="input-panel">
      <div className={`dropzone ${drag ? "dragging" : ""}`} onDragOver={e => { e.preventDefault(); setDrag(true); }}
        onDragLeave={() => setDrag(false)} onDrop={e => { e.preventDefault(); setDrag(false); elegir(e.dataTransfer.files); }}>
        <div className="stage">
          {preview ? <img src={preview} alt="Imagen seleccionada para identificar" /> : <div className="stage-empty"><span className="frame-mark" aria-hidden="true">⌜ ⌝<br />⌞ ⌟</span><h2>Trae una foto al encuadre</h2><p>Arrástrala aquí o elígela desde tu equipo.</p></div>}
          {resultado && <Cajas resultado={resultado} />}
        </div>
        <div className="file-bar"><label className="button secondary" htmlFor="imagen">Elegir imagen</label>
          <input id="imagen" type="file" accept="image/jpeg,image/png,image/webp" onChange={e => { elegir(e.target.files); e.target.value = ""; }} />
          <span>{archivo?.name || "JPEG, PNG o WebP · hasta 8 MiB"}</span></div>
      </div>
      <button className="button primary" disabled={!archivo || !permiso || estado === "subiendo"} onClick={reconocer}>
        {estado === "subiendo" ? "Subiendo y analizando…" : "Identificar rostro"}
      </button>
      {estado === "subiendo" && <p role="status">La imagen se procesa en memoria. No se guarda.</p>}
      {error && <p className="error" role="alert">{estado === "sin_rostro" ? "Sin rostro. " : ""}{error}</p>}
      <p className="hint">La consulta no añade personas a la base. Una cara nueva puede salir como «Desconocido».</p>
    </section>
    <Resultado resultado={resultado} />
  </div>;
}
