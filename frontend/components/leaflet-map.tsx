"use client";

// v2.21.0: shared Leaflet wrapper. Used at two scales:
//   - Dossier tab (full-width, ~360px tall, interactive with zoom+pan)
//   - EntitySlideOver thumbnail (~180px square, static)
// Import ONLY via next/dynamic({ ssr: false }) — react-leaflet touches window
// at module load, which SSR cannot satisfy.

import "leaflet/dist/leaflet.css";
import L from "leaflet";
import iconRetinaUrl from "leaflet/dist/images/marker-icon-2x.png";
import iconUrl from "leaflet/dist/images/marker-icon.png";
import shadowUrl from "leaflet/dist/images/marker-shadow.png";
import type { ReactNode } from "react";
import { useEffect } from "react";
import { MapContainer, Marker, Popup, TileLayer, useMap } from "react-leaflet";

// Webpack+Leaflet's classic 404: the default marker-image URLs are baked into
// the leaflet stylesheet as relative paths that don't survive bundling. Rewire
// them to the imported asset URLs so pins actually render.
type IconDefaultInternals = { _getIconUrl?: () => string };
delete (L.Icon.Default.prototype as IconDefaultInternals)._getIconUrl;
L.Icon.Default.mergeOptions({
  iconRetinaUrl: (iconRetinaUrl as unknown as { src: string }).src ?? String(iconRetinaUrl),
  iconUrl: (iconUrl as unknown as { src: string }).src ?? String(iconUrl),
  shadowUrl: (shadowUrl as unknown as { src: string }).src ?? String(shadowUrl),
});

export interface MapPoint {
  id: string;
  lat: number;
  lon: number;
  label?: string;
  popup?: ReactNode;
}

export interface LeafletMapProps {
  points: MapPoint[];
  height?: number | string;
  interactive?: boolean;
  initialZoom?: number;
  className?: string;
}

function FitToPoints({ points }: { points: MapPoint[] }) {
  const map = useMap();
  useEffect(() => {
    if (points.length === 0) return;
    if (points.length === 1) {
      map.setView([points[0].lat, points[0].lon], 6, { animate: false });
      return;
    }
    const bounds = L.latLngBounds(points.map((p) => [p.lat, p.lon] as [number, number]));
    map.fitBounds(bounds, { padding: [24, 24], maxZoom: 8, animate: false });
  }, [map, points]);
  return null;
}

export function LeafletMap({
  points,
  height = 360,
  interactive = true,
  initialZoom = 2,
  className,
}: LeafletMapProps) {
  const center: [number, number] =
    points.length > 0 ? [points[0].lat, points[0].lon] : [20, 0];
  return (
    <div
      className={className}
      style={{ height: typeof height === "number" ? `${height}px` : height }}
    >
      <MapContainer
        center={center}
        zoom={initialZoom}
        style={{ height: "100%", width: "100%", borderRadius: "0.5rem" }}
        scrollWheelZoom={interactive}
        dragging={interactive}
        doubleClickZoom={interactive}
        touchZoom={interactive}
        boxZoom={interactive}
        keyboard={interactive}
        zoomControl={interactive}
        attributionControl={interactive}
      >
        <TileLayer
          url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
          attribution='&copy; <a href="https://openstreetmap.org/copyright">OpenStreetMap</a>'
        />
        <FitToPoints points={points} />
        {points.map((p) => (
          <Marker key={p.id} position={[p.lat, p.lon]}>
            {(p.popup || p.label) && (
              <Popup>{p.popup ?? p.label}</Popup>
            )}
          </Marker>
        ))}
      </MapContainer>
    </div>
  );
}
