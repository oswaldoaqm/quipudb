import { useEffect, useRef, useState } from "react";
import { fotoUrl, MAX_NOMBRE, mensajeError, registrar, validarImagen, validarNombre, type Registro as Registrado } from "./api.ts";
import { Icon, ScanFrame } from "./UI.tsx";

/** Guarda una foto en la base para que Rikuq reconozca a esa persona (#342). */
export default function Registro({ permiso }: { permiso: boolean }) {
  const [nombre, setNombre] = useState("");
  const [archivo, setArchivo] = useState<File | null>(null);
  const [preview, setPreview] = useState("");
  const [acepta, setAcepta] = useState(false);
  const [enviando, setEnviando] = useState(false);
  const [registrado, setRegistrado] = useState<Registrado | null>(null);
  const [error, setError] = useState("");
  const [drag, setDrag] = useState(false);
  const peticion = useRef<AbortController | null>(null);
  const selector = useRef<HTMLInputElement | null>(null);
  useEffect(() => () => { peticion.current?.abort(); peticion.current = null; }, []);
  useEffect(() => {
    if (!archivo) { setPreview(""); return; }
    const url = URL.createObjectURL(archivo);
    setPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [archivo]);

  function elegir(files: FileList | null) {
    setError(""); setRegistrado(null);
    if (!files?.length) return;
    if (files.length > 1) { setError("Elige una sola foto por registro."); setArchivo(null); return; }
    const mensaje = validarImagen(files[0]);
    if (mensaje) { setError(mensaje); setArchivo(null); return; }
    setArchivo(files[0]);
  }

  const errorNombre = nombre ? validarNombre(nombre) : null;
  const listo = permiso && acepta && !!archivo && !!nombre.trim() && !errorNombre && !enviando;

  async function enviar() {
    if (!listo || !archivo) return;
    const controller = new AbortController();
    peticion.current = controller;
    const timeout = setTimeout(() => controller.abort(), 90_000);
    setEnviando(true); setError(""); setRegistrado(null);
    try {
      const respuesta = await registrar(archivo, nombre, acepta, controller.signal);
      if (peticion.current !== controller) return;
      setRegistrado(respuesta);
      setArchivo(null); setNombre(""); setAcepta(false);
    } catch (e) {
      if (peticion.current !== controller) return;
      setError(controller.signal.aborted ? "El registro agotó el tiempo de espera. Revisa el servidor." : mensajeError(e));
    } finally {
      clearTimeout(timeout);
      if (peticion.current === controller) { peticion.current = null; setEnviando(false); }
    }
  }

  const guia = !permiso ? "Confirma el permiso de arriba para registrar fotos."
    : !nombre.trim() ? "Escribe el nombre de la persona."
      : errorNombre ? errorNombre
        : !archivo ? "Elige una foto con un solo rostro, de frente y bien iluminado."
          : !acepta ? "Confirma que la persona aceptó guardar su foto."
            : enviando ? "Detectando el rostro y guardándolo en QuipuDB…" : "Todo listo. Pulsa Registrar en la base.";

  return <div className="recognition-grid">
    <section className="input-panel" aria-labelledby="registro-title">
      <div className="panel-heading"><div><span className="section-label">Nueva referencia</span><h2 id="registro-title">Registrar una foto en la base</h2></div><span className="format-tag">Un solo rostro</span></div>
      <div className="field">
        <label htmlFor="registro-nombre">Nombre de la persona</label>
        <input id="registro-nombre" className="text-input" type="text" autoComplete="off" maxLength={MAX_NOMBRE + 10}
          placeholder="Por ejemplo, Ana Quispe" value={nombre} aria-invalid={!!errorNombre} aria-describedby="registro-nombre-ayuda"
          onChange={e => { setNombre(e.target.value); setRegistrado(null); }} />
        <span id="registro-nombre-ayuda" className="field-help">Si ya existe una persona con ese nombre, la foto se suma a las suyas.</span>
      </div>
      <div className={`dropzone ${drag ? "dragging" : ""}`} onDragOver={e => { e.preventDefault(); setDrag(true); }}
        onDragLeave={() => setDrag(false)} onDrop={e => { e.preventDefault(); setDrag(false); elegir(e.dataTransfer.files); }}>
        <div className={`stage ${preview ? "" : "is-empty"}`}>
          {preview ? <img src={preview} alt="Foto que se va a registrar" /> : <div className="stage-empty"><ScanFrame /><p className="stage-title">La foto de referencia va aquí</p><p>Una persona, de frente y cerca de la cámara.</p><button type="button" className="btn btn--secondary button secondary" onClick={() => selector.current?.click()}><Icon name="image" />Elegir foto</button><span className="file-types">JPEG, PNG o WebP</span></div>}
          {enviando && <div className="analysis-status" role="status"><Icon name="scan" />Registrando…</div>}
        </div>
        <div className="file-bar"><span className="file-name"><Icon name="image" /><span>{archivo?.name || "Ninguna foto seleccionada"}</span></span>
          {archivo && <div className="file-actions"><button type="button" className="btn btn--secondary button secondary compact" onClick={() => selector.current?.click()}>Cambiar foto</button><button type="button" className="btn btn--secondary button secondary icon-button" aria-label="Quitar foto" onClick={() => setArchivo(null)}><Icon name="close" /></button></div>}
          <input ref={selector} id="registro-imagen" hidden aria-label="Seleccionar foto para registrar" type="file" accept="image/jpeg,image/png,image/webp" onChange={e => { elegir(e.target.files); e.target.value = ""; }} />
        </div>
      </div>
      <label className="check" htmlFor="registro-consentimiento">
        <input id="registro-consentimiento" type="checkbox" checked={acepta} onChange={e => setAcepta(e.target.checked)} />
        <span>La persona aceptó que su foto quede guardada en QuipuDB para esta demo. Se puede eliminar en cualquier momento desde Fotos registradas.</span>
      </label>
      <div className="analysis-actions"><button type="button" className="btn btn--primary button primary" aria-describedby="registro-guia" disabled={!listo} onClick={enviar}>
        <Icon name={enviando ? "scan" : "plus"} />{enviando ? "Registrando…" : "Registrar en la base"}
      </button></div>
      <p id="registro-guia" className="action-guidance">{guia}</p>
      {error && <div className="error" role="alert"><strong>No se pudo registrar la foto</strong><p>{error}</p></div>}
    </section>
    <aside className="result-panel" aria-live="polite" aria-labelledby="registro-resultado">
      <div className="result-heading"><span className="section-label" id="registro-resultado">Resultado del registro</span><span className="result-state">{registrado ? "Guardado" : enviando ? "En curso" : "Sin registrar"}</span></div>
      {registrado ? <div className="identity">
        <img className="reference" src={fotoUrl(registrado.foto)} alt={`Foto registrada de ${registrado.persona ?? "la persona"}`} />
        <span className="badge positive">{registrado.persona_nueva ? "Persona nueva" : "Foto añadida"}</span>
        <h2>{registrado.persona}</h2>
        <p>{registrado.persona_nueva ? "Se creó la persona con esta foto." : `Ahora tiene ${registrado.fotos} fotos de referencia.`} Ya se puede identificar en la pestaña Subir imagen o con la cámara.</p>
      </div> : <div className="empty-result"><ScanFrame /><h2>Aún no registras a nadie</h2><p>La foto se guarda en QuipuDB con su vector, y el índice HNSW la tiene en cuenta desde la siguiente consulta.</p></div>}
    </aside>
  </div>;
}
