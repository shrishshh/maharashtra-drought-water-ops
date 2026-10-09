// Leaflet map of Tuljapur taluka: villages (size = population, colour = need), filling points,
// tanker routes (one colour per tanker, fixed order; >8 tankers fold to grey), dropped and
// ineligible villages, and an optional GPS trace for a flagged claim.
import { useEffect } from "react";
import L from "leaflet";
import { Circle, CircleMarker, MapContainer, Marker, Polyline, Popup, TileLayer, Tooltip, useMap } from "react-leaflet";
import { CATEGORICAL, OTHER_GREY, needColor, type Plan, type Village } from "../data";
import { fmtInt } from "../ui";

export interface FillPoint {
  name: string;
  lat: number;
  lon: number;
}
export interface TraceOverlay {
  points: [number, number][];
  claimed: Village;
  radiusM: number;
  label: string;
}

const star = (name: string) =>
  L.divIcon({ className: "fp-icon", html: `<span aria-label="filling point ${name}">★</span>`, iconSize: [26, 26], iconAnchor: [13, 13] });
const cross = L.divIcon({ className: "drop-icon", html: "<span>✕</span>", iconSize: [14, 14], iconAnchor: [7, 7] });

function FitTo({ bounds }: { bounds: L.LatLngBounds }) {
  const map = useMap();
  const key = bounds.toBBoxString(); // refit only when the area changes, not on every render
  useEffect(() => {
    const t = setTimeout(() => {
      map.invalidateSize(); // the container may have been laid out after Leaflet measured it
      map.fitBounds(bounds, { padding: [24, 24] });
    }, 60);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, map]);
  return null;
}

export default function VillageMap({ villages, fillPoints, plan, trace }: {
  villages: Village[];
  fillPoints: FillPoint[];
  plan?: Plan;
  trace?: TraceOverlay | null;
}) {
  const byId = Object.fromEntries(villages.map((v) => [v.village_id, v]));
  const fpByName = Object.fromEntries(fillPoints.map((f) => [f.name, f]));
  const eligible = villages.filter((v) => v.eligible);
  const maxScore = Math.max(...eligible.map((v) => v.need_score));
  const served = plan?.litres_by_village ?? {};
  const base = fillPoints[0];

  const routes = (plan?.routes ?? []).filter((r) => r.events.length).map((r, i) => {
    const pts: [number, number][] = [[base.lat, base.lon]];
    for (const e of r.events) {
      const p = e.type === "deliver" ? byId[e.village_id!] : fpByName[e.place];
      if (p) pts.push([p.lat, p.lon]);
    }
    pts.push([base.lat, base.lon]);
    return { tanker: r.tanker, pts, color: i < CATEGORICAL.length ? CATEGORICAL[i] : OTHER_GREY, r };
  });

  const allPts = villages.map((v) => [v.lat, v.lon] as [number, number]);
  const bounds = trace ? L.latLngBounds([...trace.points, [trace.claimed.lat, trace.claimed.lon]]) : L.latLngBounds(allPts);

  return (
    <div className="vmap-wrap">
      <MapContainer bounds={L.latLngBounds(allPts)} className="vmap" scrollWheelZoom={false} zoomSnap={0.5}>
        <TileLayer
          url="https://tile.openstreetmap.org/{z}/{x}/{y}.png"
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
          maxZoom={18}
        />
        <FitTo bounds={bounds} />
        {!trace && routes.map((rt) => (
          <Polyline key={rt.tanker} positions={rt.pts} pathOptions={{ color: rt.color, weight: 3, opacity: 0.85 }}>
            <Tooltip sticky>{`${rt.tanker}: ${rt.r.trips} trips, ${fmtInt(rt.r.km)} km, ${fmtInt(rt.r.litres / 1000)} kL`}</Tooltip>
          </Polyline>
        ))}
        {villages.map((v) => {
          const radius = 4 + Math.sqrt(v.population) / 9;
          const got = served[v.village_id] ?? 0;
          return (
            <CircleMarker key={v.village_id} center={[v.lat, v.lon]} radius={radius}
              pathOptions={v.eligible
                ? { color: "#3b2a1a", weight: 1, fillColor: needColor(v.need_score / maxScore), fillOpacity: 0.9 }
                : { color: "#8a8985", weight: 1.5, fillOpacity: 0, dashArray: "3 3" }}>
              <Popup>
                <b>{v.name}</b> {v.name_mr && <span lang="mr">({v.name_mr})</span>}<br />
                Population {fmtInt(v.population)} (Census 2011)<br />
                {v.eligible ? <>Need {fmtInt(v.daily_need_l / 1000)} kL/day · rank {v.rank}<br />Today: <b>{fmtInt(got / 1000)} kL</b> delivered</> : <>Not tanker-eligible (own source not dry)</>}<br />
                <span className="muted">Source dry, livestock, days since last tanker: SIMULATED</span>
              </Popup>
            </CircleMarker>
          );
        })}
        {plan && eligible.filter((v) => !(served[v.village_id] > 0)).map((v) => (
          <Marker key={`x-${v.village_id}`} position={[v.lat, v.lon]} icon={cross} interactive={false} />
        ))}
        {fillPoints.map((f) => (
          <Marker key={f.name} position={[f.lat, f.lon]} icon={star(f.name)}>
            <Tooltip permanent direction="right" offset={[10, 0]}>{f.name}</Tooltip>
          </Marker>
        ))}
        {trace && (
          <>
            <Polyline positions={trace.points} pathOptions={{ color: "#1f2937", weight: 2.5 }} />
            {trace.points.map((p, i) => <CircleMarker key={i} center={p} radius={2.5} pathOptions={{ color: "#1f2937", weight: 1, fillOpacity: 1 }} />)}
            <Circle center={[trace.claimed.lat, trace.claimed.lon]} radius={trace.radiusM} pathOptions={{ color: "#c41e3a", dashArray: "6 4", fillOpacity: 0.05 }} />
            <Marker position={[trace.claimed.lat, trace.claimed.lon]} icon={L.divIcon({ className: "claim-icon", html: "<span>✚</span>", iconSize: [22, 22], iconAnchor: [11, 11] })}>
              <Tooltip permanent direction="top" offset={[0, -10]}>{trace.label}</Tooltip>
            </Marker>
          </>
        )}
      </MapContainer>
      <div className="legend vlegend" aria-hidden>
        <span><i className="dot need" /> eligible village (colour = need, size = population)</span>
        <span><i className="dot hollow" /> not eligible (source not dry)</span>
        <span>✕ eligible, no water today</span>
        <span>★ filling point</span>
        {trace ? <span><i className="line dark" /> GPS trace (±60 min) · red ring = claimed stop (300 m)</span>
          : routes.slice(0, 8).map((rt) => <span key={rt.tanker}><i className="line" style={{ background: rt.color }} /> {rt.tanker}</span>)}
        {!trace && routes.length > 8 && <span><i className="line" style={{ background: OTHER_GREY }} /> tankers 9+</span>}
      </div>
    </div>
  );
}
