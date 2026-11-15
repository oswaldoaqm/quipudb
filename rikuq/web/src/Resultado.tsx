import { fotoUrl, type Identificacion, type Rostro } from "./api.ts";

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

export function Resultado({ resultado }: { resultado: Identificacion | null }) {
  return <aside className="result-panel" aria-label="Resultado del reconocimiento" aria-live="polite">
    <p className="eyebrow">Resultado</p>
    {!resultado ? <><h2>Primero, una imagen.</h2><p>Si el rostro está registrado y supera la verificación, verás su nombre aquí. Si no, diremos «Desconocido».</p>
      <div className="empty-portrait" aria-hidden="true">⌜ &nbsp; ⌝<br />⌞ &nbsp; ⌟</div></> : <>
      {resultado.rostros.map((r, i) => <article className="identity" key={i}>
        {r.foto && <Foto ruta={r.foto} nombre={r.persona || ""} />}
        <div><span className={`badge ${r.persona ? "positive" : "neutral"}`}>{r.persona ? "Coincidencia" : "Sin coincidencia"}</span>
          <h2>{r.persona || "Desconocido"}</h2>
          <p>{r.persona ? "Foto de referencia registrada" : "No hay una identidad confirmada dentro del umbral."}</p>
        </div>
        <dl className="metrics"><div><dt>Distancia coseno ↓</dt><dd>{r.distancia?.toFixed(4) ?? "Sin candidatos"}</dd></div>
          <div><dt title="Margen al umbral; no es una probabilidad de acierto">Margen de confianza</dt><dd>{(r.confianza * 100).toFixed(1)} %</dd></div></dl>
      </article>)}
      <div className="timings"><h3>Dos tiempos, sin mezclarlos</h3>
        <dl className="metrics"><div><dt>Búsqueda · HTTP motor</dt><dd>{resultado.ms_motor.toFixed(2)} <small>ms</small></dd></div>
          <div><dt>Servidor · total</dt><dd>{resultado.ms_total.toFixed(2)} <small>ms</small></dd></div></dl>
        <p>El total incluye detectar y describir el rostro. El tiempo del motor incluye la ida y vuelta local.</p>
      </div>
    </>}
  </aside>;
}

function Foto({ ruta, nombre }: { ruta: string; nombre: string }) {
  return <img className="reference" src={fotoUrl(ruta)} alt={`Foto registrada de ${nombre}`} onError={e => { e.currentTarget.hidden = true; }} />;
}
