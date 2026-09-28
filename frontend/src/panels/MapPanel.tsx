import { Icon, latLngBounds } from "leaflet";
import markerIcon from "leaflet/dist/images/marker-icon.png";
import markerIconRetina from "leaflet/dist/images/marker-icon-2x.png";
import markerShadow from "leaflet/dist/images/marker-shadow.png";
import { useEffect, useMemo } from "react";
import { MapContainer, Marker, Popup, TileLayer, useMap } from "react-leaflet";

import type { QueryResult } from "@/api/types";
import { Panel } from "@/components/Panel";
import { extractSpatialPoints, isPointValue, type SpatialPoint } from "@/lib/spatial";

import "leaflet/dist/leaflet.css";

interface MapPanelProps {
  resultado: QueryResult | null;
  hayError: boolean;
  ejecutando: boolean;
}

// Importar los assets evita la deteccion de rutas de Icon.Default, que puede
// fallar cuando Vite renombra los PNG al compilar para produccion.
const RESULT_ICON = new Icon({
  iconUrl: markerIcon,
  iconRetinaUrl: markerIconRetina,
  shadowUrl: markerShadow,
  iconSize: [25, 41],
  iconAnchor: [12, 41],
  popupAnchor: [1, -34],
  shadowSize: [41, 41],
  className: "query-marker",
});

function FitResults({ points }: { points: SpatialPoint[] }) {
  const map = useMap();
  useEffect(() => {
    const fit = () => {
      map.invalidateSize({ pan: false });
      if (points.length === 1) {
        map.setView(points[0].position, 15, { animate: false });
      } else if (points.length > 1) {
        map.fitBounds(latLngBounds(points.map((point) => point.position)), {
          padding: [35, 45],
          maxZoom: 15,
          animate: false,
        });
      }
    };
    fit();
    // Tambien cuando cambia el layout, sin depender de un resize de ventana.
    let frame = 0;
    const observer = new ResizeObserver(() => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(fit);
    });
    observer.observe(map.getContainer());
    return () => {
      observer.disconnect();
      cancelAnimationFrame(frame);
    };
  }, [map, points]);
  return null;
}

function formatCell(value: unknown): string {
  if (value === null || value === undefined) return "NULL";
  if (isPointValue(value)) return `POINT(${value.latitude}, ${value.longitude})`;
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

/** Cualquier consulta con POINT se dibuja, independientemente del algoritmo. */
export function MapPanel({ resultado, hayError, ejecutando }: MapPanelProps) {
  const spatial = useMemo(() => extractSpatialPoints(resultado), [resultado]);
  const { points, pointColumns, invalidCount } = spatial;
  let message: string | null = null;
  if (hayError) {
    message = "La consulta no se ejecutó. Revisa el error junto al editor.";
  } else if (ejecutando) {
    message = "Ejecutando consulta…";
  } else if (!resultado) {
    message = "Ejecuta una consulta con una columna POINT para ver sus puntos.";
  } else if (pointColumns.length === 0) {
    message = "El resultado no contiene columnas POINT. Incluye una columna de coordenadas en el SELECT.";
  } else if (resultado.rows.length === 0) {
    message = "La consulta no devolvió resultados. No hay puntos que mostrar.";
  } else if (points.length === 0) {
    message = "No hay coordenadas válidas. Se requiere latitud entre −90 y 90 y longitud entre −180 y 180, con valores numéricos finitos.";
  }

  return (
    <Panel
      titulo="Mapa"
      nota={message ? undefined : `${points.length.toLocaleString("es-PE")} ${points.length === 1 ? "punto" : "puntos"}`}
    >
      {message ? (
        <p role="status" className="p-3 text-xs text-muted-foreground">{message}</p>
      ) : resultado ? (
        <div className="flex h-full min-h-[260px] flex-col">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b px-3 py-2 text-xs" role="status">
            <span className="flex items-center gap-2">
              <span className="size-2.5 rounded-full bg-blue-600" aria-hidden="true" />
              Puntos de la consulta · {pointColumns.length} {pointColumns.length === 1 ? "columna POINT" : "columnas POINT"}
            </span>
            {invalidCount > 0 && (
              <span className="text-muted-foreground">
                {invalidCount} {invalidCount === 1 ? "valor POINT omitido por coordenadas inválidas" : "valores POINT omitidos por coordenadas inválidas"}
              </span>
            )}
          </div>
          <MapContainer
            center={points[0].position}
            zoom={15}
            maxZoom={19}
            className="query-map min-h-0 flex-1"
            aria-label="Mapa de los puntos de la consulta"
          >
            <TileLayer
              url="https://tile.openstreetmap.org/{z}/{x}/{y}.png"
              attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
              maxZoom={19}
            />
            <FitResults points={points} />
            {points.map((point) => (
              <Marker
                key={point.key}
                position={point.position}
                icon={RESULT_ICON}
                title={`Fila ${point.rowIndex + 1} · ${point.columnName}`}
                alt={`Resultado ${point.rowIndex + 1}, ${point.columnName}`}
                riseOnHover
              >
                <Popup minWidth={240} maxWidth={340} maxHeight={220}>
                  <strong>Fila {point.rowIndex + 1} · {point.columnName}</strong>
                  <dl className="mt-2 grid grid-cols-[minmax(60px,auto)_1fr] gap-x-3 gap-y-1 text-xs">
                    {resultado.columns.map((column, index) => (
                      <div key={index} className="contents">
                        <dt className="font-semibold break-words">{column}</dt>
                        <dd className="min-w-0 break-words">{formatCell(point.row[index])}</dd>
                      </div>
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
