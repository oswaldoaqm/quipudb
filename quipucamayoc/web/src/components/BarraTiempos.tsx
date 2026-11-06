/**
 * En que se fue el tiempo de una respuesta: reescritura (Gemini rapido),
 * recuperacion (QuipuDB) y generacion (Gemini). Barra apilada de una sola
 * fila: colores categoricos 1-3 de la paleta validada, en orden fijo, con
 * 2px de separacion, extremos redondeados y leyenda con los valores (el aqua
 * queda bajo 3:1 contra el fondo, asi que el valor siempre esta escrito).
 */

import { useState } from "react";

import type { Tiempos } from "@/api/types";

const ETAPAS = [
  { clave: "reescritura_ms", nombre: "Reescritura (Gemini)", color: "#2a78d6" },
  { clave: "recuperacion_ms", nombre: "Recuperación (QuipuDB)", color: "#eb6834" },
  { clave: "generacion_ms", nombre: "Generación (Gemini)", color: "#1baf7a" },
] as const;

export function formatearMs(ms: number): string {
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${ms < 10 ? ms.toFixed(1) : Math.round(ms)} ms`;
}

export function BarraTiempos({ tiempos }: { tiempos: Tiempos }) {
  const [activa, setActiva] = useState<string | null>(null);
  const suma = ETAPAS.reduce((total, e) => total + tiempos[e.clave], 0) || 1;
  const visibles = ETAPAS.filter((e) => tiempos[e.clave] > 0);

  return (
    <figure className="space-y-2">
      <figcaption className="flex items-baseline justify-between text-sm">
        <span className="font-medium text-stone-900">Tiempo de la respuesta</span>
        <span className="text-stone-600">total {formatearMs(tiempos.total_ms)}</span>
      </figcaption>
      <div className="relative">
        <div className="flex h-3 w-full gap-[2px]" role="img" aria-label="Reparto del tiempo por etapa">
          {visibles.map((e, i) => {
            const valor = tiempos[e.clave];
            const primera = i === 0;
            const ultima = i === visibles.length - 1;
            return (
              <div
                key={e.clave}
                className="relative min-w-[4px] cursor-default transition-opacity"
                style={{
                  flexGrow: valor / suma,
                  flexBasis: 0,
                  backgroundColor: e.color,
                  opacity: activa && activa !== e.clave ? 0.35 : 1,
                  borderRadius: `${primera ? 4 : 0}px ${ultima ? 4 : 0}px ${ultima ? 4 : 0}px ${primera ? 4 : 0}px`,
                }}
                onMouseEnter={() => setActiva(e.clave)}
                onMouseLeave={() => setActiva(null)}
              >
                {/* El area de hover es mas alta que la barra: un segmento de
                    4px tambien se puede apuntar. */}
                <span className="absolute -inset-y-2 inset-x-0" />
                {activa === e.clave && (
                  <span className="pointer-events-none absolute bottom-5 left-1/2 z-10 -translate-x-1/2 whitespace-nowrap rounded-md bg-stone-900 px-2 py-1 text-xs text-white shadow">
                    {e.nombre}: {formatearMs(valor)} ({Math.round((valor / suma) * 100)}%)
                  </span>
                )}
              </div>
            );
          })}
        </div>
      </div>
      <ul className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-stone-600">
        {ETAPAS.map((e) => (
          <li key={e.clave} className="flex items-center gap-1.5">
            <span className="size-2.5 rounded-sm" style={{ backgroundColor: e.color }} />
            {e.nombre} <span className="font-medium text-stone-900">{formatearMs(tiempos[e.clave])}</span>
          </li>
        ))}
      </ul>
    </figure>
  );
}
