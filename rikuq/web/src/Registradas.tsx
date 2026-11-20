import { useCallback, useEffect, useState } from "react";
import { eliminarRostro, fotoUrl, listarSubidos, mensajeError, type RostroSubido } from "./api.ts";
import { Icon, ScanFrame } from "./UI.tsx";

/**
 * Las fotos guardadas desde Registrar, con la opción de quitarlas de la base
 * (#342). No pide el permiso de arriba: retirar una foto siempre se puede.
 */
export default function Registradas() {
  const [fotos, setFotos] = useState<RostroSubido[] | null>(null);
  const [cargando, setCargando] = useState(false);
  const [confirmar, setConfirmar] = useState<number | null>(null);
  const [borrando, setBorrando] = useState<number | null>(null);
  const [aviso, setAviso] = useState("");
  const [error, setError] = useState("");

  const cargar = useCallback(async () => {
    setCargando(true); setError("");
    try { setFotos(await listarSubidos()); }
    catch (e) { setError(mensajeError(e)); }
    finally { setCargando(false); }
  }, []);
  useEffect(() => { void cargar(); }, [cargar]);

  async function eliminar(foto: RostroSubido) {
    setBorrando(foto.id); setError(""); setAviso("");
    try {
      await eliminarRostro(foto.id);
      setFotos(actuales => actuales?.filter(f => f.id !== foto.id) ?? null);
      setAviso(`Se eliminó la foto ${foto.id} de ${foto.persona ?? "la persona"}.`);
    } catch (e) {
      setError(mensajeError(e));
    } finally {
      setBorrando(null); setConfirmar(null);
    }
  }

  return <section className="result-panel gallery-panel" aria-labelledby="registradas-title">
    <div className="result-heading">
      <div><span className="section-label">Base de Rikuq</span><h2 id="registradas-title">Fotos registradas desde la app</h2></div>
      <button type="button" className="btn btn--secondary button secondary compact" onClick={() => void cargar()} disabled={cargando}>
        <Icon name="refresh" />{cargando ? "Actualizando…" : "Actualizar"}
      </button>
    </div>
    <p className="privacy-note">Solo aparecen las fotos guardadas con Registrar. Las del dataset se cargan con su propio comando y no se borran desde aquí.</p>
    <p className="sr-status" role="status">{aviso}</p>
    {error && <div className="error" role="alert"><strong>No se pudo completar la acción</strong><p>{error}</p></div>}
    {fotos === null ? (!error && <p className="action-guidance">Cargando fotos…</p>)
      : fotos.length === 0 ? <div className="empty-result"><ScanFrame /><h2>No hay fotos registradas</h2><p>Registra a alguien en la pestaña Registrar y aparecerá aquí.</p></div>
        : <ul className="gallery">
          {fotos.map(foto => <li key={foto.id} className="photo-card">
            <img src={fotoUrl(foto.foto)} alt={`Foto registrada de ${foto.persona ?? "persona sin nombre"}`} loading="lazy" />
            <div className="photo-body">
              <strong>{foto.persona ?? "Sin nombre"}</strong>
              <span>Foto {foto.id}</span>
            </div>
            <div className="photo-actions">
              {confirmar === foto.id
                ? <>
                  <button type="button" className="btn button danger compact" disabled={borrando === foto.id} onClick={() => void eliminar(foto)}>
                    <Icon name="trash" />{borrando === foto.id ? "Eliminando…" : "Sí, eliminar"}
                  </button>
                  <button type="button" className="btn btn--secondary button secondary compact" disabled={borrando === foto.id} onClick={() => setConfirmar(null)}>Cancelar</button>
                </>
                : <button type="button" className="btn btn--secondary button secondary compact" aria-label={`Eliminar la foto ${foto.id} de ${foto.persona ?? "la persona"}`} onClick={() => { setConfirmar(foto.id); setAviso(""); }}>
                  <Icon name="trash" />Eliminar
                </button>}
            </div>
          </li>)}
        </ul>}
  </section>;
}
