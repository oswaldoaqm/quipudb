import type { Circle as LeafletCircle } from "leaflet";
import { latLngBounds } from "leaflet";
import { useEffect, useRef } from "react";
import { Circle, CircleMarker, Pane, Polygon, Popup, useMap } from "react-leaflet";
import type { PolygonContext, RadiusContext } from "@/api/types";
import type { SpatialView } from "@/lib/spatial";

interface SpatialRegionProps {
  context: RadiusContext | PolygonContext | null;
  view: SpatialView;
}

/** Region y encuadre reciben decisiones estructuradas, nunca SQL ni puntos base. */
export function SpatialRegion({ context, view }: SpatialRegionProps) {
  const map = useMap();
  const circleRef = useRef<LeafletCircle | null>(null);
  useEffect(() => {
    const fit = () => {
      map.invalidateSize({ pan: false });
      if (view.kind === "circle") {
        const circle = circleRef.current;
        if (circle) map.fitBounds(circle.getBounds(), { padding: [24, 24], maxZoom: 18, animate: false });
      } else if (view.kind === "polygon") {
        map.fitBounds(latLngBounds(view.vertices), { padding: [24, 24], maxZoom: 18, animate: false });
      } else if (view.kind === "points" && view.positions.length) {
        if (view.positions.length === 1) map.setView(view.positions[0], 15, { animate: false });
        else map.fitBounds(latLngBounds(view.positions), { padding: [35, 45], maxZoom: 15, animate: false });
      }
    };
    // Espera al montaje/proyeccion de Circle antes de pedir sus limites.
    let frame = requestAnimationFrame(fit);
    const observer = new ResizeObserver(() => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(fit);
    });
    observer.observe(map.getContainer());
    return () => { observer.disconnect(); cancelAnimationFrame(frame); };
  }, [map, view]);

  return (
    <>
      {view.kind === "circle" && (
        <Circle
          ref={circleRef}
          center={view.center}
          radius={view.radius}
          interactive={false}
          pathOptions={{ color: "#2563eb", weight: 2, fillOpacity: 0.09, dashArray: context?.kind === "radius" && context.operator === "<" ? "5 5" : undefined }}
        />
      )}
      {view.kind === "polygon" && (
        <Polygon
          positions={view.vertices}
          interactive={false}
          pathOptions={{ color: "#2563eb", weight: 2, fillOpacity: 0.09 }}
        />
      )}
      {context?.kind === "radius" && (
        <Pane name="search-reference" style={{ zIndex: 650 }}>
          <CircleMarker
            center={[context.center.latitude, context.center.longitude]}
            radius={7}
            pathOptions={{ color: "#78350f", fillColor: "#fbbf24", fillOpacity: 1, weight: 2 }}
          >
            <Popup minWidth={200}>
              <strong>Referencia de búsqueda</strong>
              <div>POINT({context.center.latitude}, {context.center.longitude})</div>
              <div>{context.metric} · {context.operator} {context.radius} {context.unit === "meters" ? "m" : "grados"}</div>
            </Popup>
          </CircleMarker>
        </Pane>
      )}
    </>
  );
}
