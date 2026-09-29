import { Icon } from "leaflet";
import markerIcon from "leaflet/dist/images/marker-icon.png";
import markerIconRetina from "leaflet/dist/images/marker-icon-2x.png";
import markerShadow from "leaflet/dist/images/marker-shadow.png";
import { useMemo } from "react";
import { CircleMarker, MapContainer, Marker, Pane, Popup, TileLayer } from "react-leaflet";

import type { QueryResult } from "@/api/types";
import { Panel } from "@/components/Panel";
import { SpatialRegion } from "@/components/SpatialRegion";
import type { SpatialBase } from "@/hooks/useSpatialContext";
import {
  basePointsOutsideResults, extractSpatialPoints, isPointValue,
  mapMessage, radiusContext, spatialView,
} from "@/lib/spatial";

import "leaflet/dist/leaflet.css";

interface MapPanelProps {
  resultado: QueryResult | null;
  hayError: boolean;
  ejecutando: boolean;
  base?: SpatialBase;
}

// Imports explicitos: Vite conserva los PNG incluso en el build de produccion.
const RESULT_ICON = new Icon({
  iconUrl: markerIcon, iconRetinaUrl: markerIconRetina, shadowUrl: markerShadow,
  iconSize: [25, 41], iconAnchor: [12, 41], popupAnchor: [1, -34], shadowSize: [41, 41],
  className: "query-marker",
});

function formatCell(value: unknown): string {
  if (value === null || value === undefined) return "NULL";
  if (isPointValue(value)) return `POINT(${value.latitude}, ${value.longitude})`;
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

/** Presentacion del resultado y su contexto; no ejecuta consultas ni filtra distancias. */
export function MapPanel({ resultado, hayError, ejecutando, base }: MapPanelProps) {
  const { points, invalidCount, pointColumns } = useMemo(() => extractSpatialPoints(resultado), [resultado]);
  const context = radiusContext(resultado);
  const view = useMemo(() => spatialView(context, points), [context, points]);
  const background = useMemo(() => basePointsOutsideResults(base?.result ?? null, resultado, base?.table ?? null), [base?.result, base?.table, resultado]);
  const message = mapMessage(resultado, ejecutando, hayError);
  const count = resultado?.rows.length ?? 0;
  const center = view.kind === "circle" ? view.center : view.kind === "points" ? view.positions[0] : [0, 0] as [number, number];

  return (
    <Panel titulo="Mapa" nota={message ? undefined : `${count} ${count === 1 ? "coincidencia" : "coincidencias"} · ${points.length} ${points.length === 1 ? "punto" : "puntos"}`}>
      {message ? (
        <p role="status" className="p-3 text-xs text-muted-foreground">{message}</p>
      ) : resultado ? (
        <div className="flex h-full min-h-[260px] flex-col">
          <div className="max-h-32 shrink-0 overflow-auto border-b px-3 py-2 text-[11px]" role="status">
            <ul aria-label="Leyenda del mapa" className="flex flex-wrap items-center gap-x-3 gap-y-1">
              {base?.result && <li className="flex items-center gap-1"><span className="size-2.5 rounded-full border border-slate-600 bg-slate-400" aria-hidden="true" />Base · {background.points.length}</li>}
              <li className="flex items-center gap-1"><span className="size-2.5 rounded-full bg-blue-600" aria-hidden="true" />Resultados · {points.length}</li>
              {context && <li className="flex items-center gap-1"><span className="size-2.5 rounded-full border border-amber-900 bg-amber-400" aria-hidden="true" />Referencia</li>}
              {view.kind === "circle" && <li className="flex items-center gap-1"><span className="size-3 rounded-full border border-blue-600 bg-blue-100" aria-hidden="true" />Región · {context?.operator} {context?.radius} m</li>}
            </ul>
            {resultado.rows.length === 0 && <p className="mt-1 font-medium">0 coincidencias. Se muestra la búsqueda actual.</p>}
            {resultado.rows.length > 0 && pointColumns.length === 0 && <p className="mt-1">La proyección no contiene POINT; incluye la columna espacial para ver los resultados.</p>}
            {context?.metric === "EUCLIDEAN" && <p className="mt-1 text-muted-foreground">Radio euclidiano: {context.radius} grados. La región no se representa como círculo geográfico.</p>}
            {invalidCount > 0 && <p className="mt-1 text-muted-foreground">{invalidCount} valores POINT omitidos por coordenadas inválidas.</p>}
            {resultado.spatial_context && !context && <p className="mt-1 text-muted-foreground">Contexto espacial inválido: se muestran solo los puntos válidos.</p>}
            {base?.message && <p className="mt-1 text-muted-foreground">{base.message}</p>}
            {base?.result && !background.hasIdentity && points.length > 0 && <p className="mt-1 text-muted-foreground">Sin PK en la proyección no se asocian registros base con resultados.</p>}
          </div>
          <MapContainer center={center} zoom={15} maxZoom={19} className="query-map min-h-0 flex-1" aria-label="Mapa de los puntos de la consulta">
            <TileLayer url="https://tile.openstreetmap.org/{z}/{x}/{y}.png" attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors' maxZoom={19} />
            <SpatialRegion context={context} view={view} />
            <Pane name="base-points" style={{ zIndex: 410 }}>
              {background.points.map(point => (
                <CircleMarker key={point.key} center={point.position} radius={5} pathOptions={{ color: "#475569", fillColor: "#94a3b8", fillOpacity: 0.8, weight: 1 }}>
                  <Popup><strong>Punto base · {point.columnName}</strong><dl>{base?.result?.columns.map((column, index) => <div key={index}><dt className="inline font-semibold">{column}: </dt><dd className="inline">{formatCell(point.row[index])}</dd></div>)}</dl></Popup>
                </CircleMarker>
              ))}
            </Pane>
            {points.map(point => (
              <Marker key={point.key} position={point.position} icon={RESULT_ICON} title={`Fila ${point.rowIndex + 1} · ${point.columnName}`} alt={`Resultado ${point.rowIndex + 1}, ${point.columnName}`} riseOnHover>
                <Popup minWidth={240} maxWidth={340} maxHeight={220}>
                  <strong>Fila {point.rowIndex + 1} · {point.columnName}</strong>
                  <dl className="mt-2 grid grid-cols-[minmax(60px,auto)_1fr] gap-x-3 gap-y-1 text-xs">
                    {resultado.columns.map((column, index) => (
                      <div key={index} className="contents"><dt className="font-semibold break-words">{column}</dt><dd className="min-w-0 break-words">{formatCell(point.row[index])}</dd></div>
                    ))}
                  </dl>
                </Popup>
              </Marker>
            ))}
          </MapContainer>
        </div>
      ) : null}
    </Panel>
  );
}
