import { useEffect, useRef, useState } from "react";
import { ApiError, identificar, mensajeError, type Identificacion } from "./api.ts";
import { apagarStream, BucleCamara, errorCamara, Estabilizador } from "./camara.ts";
import { Cajas, Resultado } from "./Resultado.tsx";

export default function Camara({ permiso }: { permiso: boolean }) {
  const video = useRef<HTMLVideoElement>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const intento = useRef(0);
  const [stream, setStream] = useState<MediaStream | null>(null);
  const [iniciando, setIniciando] = useState(false);
  const [resultado, setResultado] = useState<Identificacion | null>(null);
  const [error, setError] = useState("");
  const [estado, setEstado] = useState("Cámara apagada");

  function apagar() {
    ++intento.current;
    apagarStream(streamRef.current);
    streamRef.current = null;
    setStream(null); setResultado(null); setIniciando(false); setEstado("Cámara apagada"); setError("");
  }

  useEffect(() => () => { ++intento.current; apagarStream(streamRef.current); }, []);
  useEffect(() => { if (!permiso) apagar(); }, [permiso]);
  useEffect(() => {
    const elemento = video.current;
    if (!stream || !elemento) return;
    let activo = true;
    elemento.srcObject = stream;
    const estabilizador = new Estabilizador();
    const canvas = document.createElement("canvas");
    const loop = new BucleCamara<Identificacion>(async signal => {
      if (elemento.readyState < 2 || !elemento.videoWidth) return null;
      canvas.width = Math.min(1280, elemento.videoWidth);
      canvas.height = Math.round(canvas.width * elemento.videoHeight / elemento.videoWidth);
      canvas.getContext("2d")!.drawImage(elemento, 0, 0, canvas.width, canvas.height);
      const blob = await new Promise<Blob | null>(resolve => canvas.toBlob(resolve, "image/jpeg", .85));
      if (!blob || signal.aborted) return null;
      setEstado("Reconociendo · máximo 3 cuadros/s");
      return identificar(blob, signal);
    }, valor => {
      setResultado({ ...valor, rostros: estabilizador.aplicar(valor.rostros) });
      setError(""); setEstado("Reconocimiento en vivo · máximo 3 cuadros/s");
    }, fallo => {
      estabilizador.limpiar(); setResultado(null);
      if (fallo instanceof ApiError && fallo.codigo === "sin_rostro") {
        setError(""); setEstado("Sin rostro en el encuadre");
      } else { setError(mensajeError(fallo)); setEstado("No se pudo analizar el cuadro"); }
    });
    const terminado = () => apagar();
    stream.getVideoTracks().forEach(track => track.addEventListener("ended", terminado));
    elemento.play().then(() => { if (activo) loop.iniciar(); }).catch(() => {
      if (activo) setError("No se pudo reproducir la cámara. Apágala y vuelve a intentar.");
    });
    return () => {
      activo = false;
      loop.detener();
      stream.getVideoTracks().forEach(track => track.removeEventListener("ended", terminado));
      elemento.pause(); elemento.srcObject = null;
    };
  }, [stream]);

  async function encender() {
    if (!permiso || iniciando || streamRef.current) return;
    const numero = ++intento.current;
    setIniciando(true); setError("");
    try {
      if (!navigator.mediaDevices?.getUserMedia) throw new Error("contexto inseguro");
      const nuevo = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "user", width: { ideal: 1280 } }, audio: false });
      if (numero !== intento.current) { apagarStream(nuevo); return; }
      streamRef.current = nuevo; setStream(nuevo); setEstado("Preparando cámara…");
    } catch (e) { if (numero === intento.current) setError(errorCamara(e)); }
    finally { if (numero === intento.current) setIniciando(false); }
  }

  return <div className="recognition-grid">
    <section className="input-panel">
      <div className="dropzone"><div className="stage">
        <video ref={video} muted playsInline aria-label="Cámara de reconocimiento facial" hidden={!stream} />
        {!stream && <div className="stage-empty"><span className="frame-mark" aria-hidden="true">⌜ ⌝<br />⌞ ⌟</span><h2>Tu cámara, solo cuando decidas.</h2><p>El navegador te pedirá permiso antes de encenderla.</p></div>}
        {resultado && <Cajas resultado={resultado} />}
      </div></div>
      <div className="camera-actions"><button className="button primary" disabled={!permiso || iniciando || !!stream} onClick={encender}>{iniciando ? "Esperando permiso…" : "Encender cámara"}</button>
        <button className="button secondary" disabled={!stream && !iniciando} onClick={apagar}>Apagar cámara</button></div>
      <p className="hint" role="status">{estado}</p>
      {error && <p role="alert" className="error">{error}</p>}
      <p className="hint">Una sola petición a la vez. Sin audio ni grabación. Los cuadros se descartan tras analizarlos. Los cambios de nombre se confirman durante tres cuadros; «Desconocido» aparece de inmediato.</p>
    </section>
    <Resultado resultado={resultado} />
  </div>;
}
