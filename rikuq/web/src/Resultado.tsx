import { fotoUrl, type Identificacion, type Rostro } from "./api.ts";
import { useEffect, useState } from "react";
import { Icon, ScanFrame } from "./UI.tsx";

export function Cajas({ resultado, rostros = resultado.rostros }: { resultado: Identificacion; rostros?: Rostro[] }) {
  return <svg className="overlay" viewBox={`0 0 ${resultado.ancho} ${resultado.alto}`} aria-hidden="true">
    {rostros.map((r, i) => {
      const [x, y, w, h] = r.caja;
      const size = Math.max(14, resultado.ancho / 42);
      return <g key={i} className={r.persona ? "known" : "unknown"}>
        <rect x={x} y={y} width={w} height={h} fill="none" strokeWidth={Math.max(2, resultado.ancho / 300)} />
        <text x={Math.max(0, x)} y={Math.max(size, y - size / 3)} fontSize={size}>
          {r.persona || "Desconocido"} · {r.distancia === null ? "sin candidatos" : r.distancia.toFixed(3)}
        </text>
      </g>;
    })}
  </svg>;
}

export function Resultado({ resultado, analizando = false, vivo = false }: {
  resultado: Identificacion | null; analizando?: boolean; vivo?: boolean;
}) {
  const umbral = typeof resultado?.umbral === "number" && Number.isFinite(resultado.umbral)
    && resultado.umbral > 0 ? resultado.umbral : null;
  return <aside className="result-panel" aria-label="Resultado del reconocimiento" aria-live={vivo ? "off" : "polite"} aria-busy={analizando}>
    <div className="result-heading"><span className="section-label">Resultado del análisis</span><span className="result-state">{analizando ? "En proceso" : resultado ? `${resultado.rostros.length} ${resultado.rostros.length === 1 ? "rostro" : "rostros"}` : "En espera"}</span></div>
    {!resultado ? <div className="empty-result"><ScanFrame camera={vivo} /><h2>{analizando ? "Analizando tu imagen" : "El resultado aparecerá aquí"}</h2>
      <p>{analizando ? "Rikuq está procesando la imagen y consultando las referencias en QuipuDB." : vivo ? "Enciende la cámara para comparar los rostros del encuadre con las referencias registradas." : "Selecciona una foto y pulsa Identificar rostro para comparar con las referencias registradas."}</p>
      <div className="result-legend"><span><i className="legend-dot positive" />Coincidencia aceptada</span><span><i className="legend-dot neutral" />Desconocido</span></div>
      <p className="empty-note">Si no hay una coincidencia dentro del umbral, el rostro se muestra como Desconocido.</p>
    </div> : <>
      {resultado.rostros.map((r, i) => {
        // En vivo no: con tres cuadros por segundo las fotos se recargarian sin parar.
        const fotos = !vivo && r.persona ? r.coincidencias ?? [] : [];
        const principal = fotos[0]?.foto ?? r.foto;
        return <article className="identity" key={i}>
        {r.persona && principal && <Foto ruta={principal} nombre={r.persona} />}
        <div><span className={`badge ${r.persona ? "positive" : "neutral"}`}>{r.persona ? "Coincidencia" : "Sin coincidencia"}</span>
          <h2>{r.persona || "Desconocido"}</h2>
          <p>{r.persona ? fotos.length > 1 ? `${fotos.length} fotos de referencia coinciden` : "Foto de referencia registrada" : r.distancia === null ? "No hay referencias disponibles para comparar." : "La referencia más cercana no pasa el umbral de aceptación."}</p>
        </div>
        <dl className="metrics"><div><dt>Parecido</dt><dd>{r.distancia === null ? "Sin candidatos" : `${((1 - r.distancia) * 100).toFixed(1)} %`}</dd></div>
          <div><dt>Distancia coseno ↓</dt><dd>{r.distancia?.toFixed(4) ?? "Sin candidatos"}</dd></div>
          <div><dt>Margen al umbral</dt><dd>{r.persona ? `${(r.confianza * 100).toFixed(1)} %` : "No aplica"}</dd></div></dl>
        <p className="threshold-note">{umbral === null ? "Umbral de aceptación no informado por el servidor."
          : <>Umbral de aceptación: <strong>{umbral.toFixed(4)}</strong>. Menor distancia indica mayor parecido.</>}</p>
        <p className="metric-explanation">El <strong>parecido</strong> es la similitud coseno que devuelve el motor: cuánto se parecen las dos caras. Dos fotos distintas de la misma persona rondan el 59%; un desconocido no pasó del 31% en una medición sobre 400 personas, y lo habitual es el 1%. El <strong>margen</strong> mide la separación respecto al límite de aceptación. <strong>No es una probabilidad ni un porcentaje de acierto</strong>, y el parecido tampoco.</p>
        {r.fiable === false && <p className="quality-warning">Rostro pequeño: acércate a la cámara o usa una imagen de mayor resolución para una comparación más fiable.</p>}
        {!r.persona && <p>{r.distancia === null ? "Sin candidatos para comparar." : "No hay una coincidencia aceptada; el margen no aplica."}</p>}
        {fotos.length > 1 && <div className="matches">
          <h3>Fotos de {r.persona} que coinciden ({fotos.length})</h3>
          <ul>{fotos.map(c => <li key={c.rostro_id}>
            <Foto ruta={c.foto} nombre={r.persona ?? ""} clase="match-photo" />
            <span>Distancia {c.distancia.toFixed(4)}</span>
          </li>)}</ul>
          <p>Entre los rostros más parecidos de la base, las que están dentro del umbral, de la más parecida a la menos.</p>
        </div>}
      </article>;
      })}
      <div className="timings"><h3><Icon name="clock" />Tiempos del análisis</h3>
        <dl className="metrics"><div><dt>Búsqueda · HTTP motor</dt><dd>{resultado.ms_motor.toFixed(2)} <small>ms</small></dd></div>
          <div><dt>Servidor · proceso</dt><dd>{(resultado.ms_proceso ?? resultado.ms_total).toFixed(2)} <small>ms</small></dd></div>
          {resultado.ms_recepcion !== undefined &&
            <div><dt>Recepción · subida</dt><dd>{resultado.ms_recepcion.toFixed(2)} <small>ms</small></dd></div>}</dl>
        <p>El proceso incluye detectar y describir el rostro, y la búsqueda en el motor. La recepción es lo que tarda el navegador en subir la imagen: con la cámara en vivo suele ser lo más lento, y no es trabajo del servidor.</p>
      </div>
    </>}
  </aside>;
}

function Foto({ ruta, nombre, clase = "reference" }: { ruta: string; nombre: string; clase?: string }) {
  const [fallo, setFallo] = useState(false);
  useEffect(() => { setFallo(false); }, [ruta]);
  return fallo ? <p>Foto de referencia no disponible en esta máquina.</p>
    : <img className={clase} src={fotoUrl(ruta)} alt={`Foto registrada de ${nombre}`} width="80" height="96" onError={() => setFallo(true)} />;
}
