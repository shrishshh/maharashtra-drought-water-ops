// SVG map of the EPA Net3 sample network (node x/y are not lat/lon, so no tile map).
import net3 from "../data/net3.json";
import { RDYLGN, ratioColor } from "../data";

type Net3 = typeof net3;
const NET = net3 as Net3 & {
  nodes: Record<string, { x: number; y: number; type: string }>;
  wards: Record<string, { polygon: number[][]; label: number[]; inlet_pipes: string[] }>;
};

// Net3 coordinates span only ~37 x 31 units: scale to ~1000 so strokes and labels are in proportion.
const { minX, maxX, minY, maxY } = NET.bounds;
const S = 1000 / Math.max(maxX - minX, maxY - minY);
const PAD = 60;
const W = (maxX - minX) * S + 2 * PAD;
const H = (maxY - minY) * S + 2 * PAD;
const sx = (x: number) => (x - minX) * S + PAD;
const sy = (y: number) => (maxY - y) * S + PAD; // flip: network y grows upwards

export const WARD_NAMES = Object.keys(NET.wards).sort();
export const junctionWard = NET.junction_to_ward as Record<string, string>;

interface Props {
  title: string;
  ratios?: Record<string, number>;
  dryThreshold?: number;
  wardFill?: Record<string, string>;
  wardNote?: Record<string, string>;
  markers?: { node: string; label: string }[];
  ariaLabel: string;
}

export default function NetworkMap({ title, ratios, dryThreshold = 0.5, wardFill, wardNote, markers = [], ariaLabel }: Props) {
  const r = Math.max(W, H) / 110;
  return (
    <figure className="netmap">
      <figcaption>{title}</figcaption>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={ariaLabel}>
        {Object.entries(NET.wards).map(([w, ward]) => (
          <polygon
            key={w}
            points={ward.polygon.map(([x, y]) => `${sx(x)},${sy(y)}`).join(" ")}
            className="ward-outline"
            style={wardFill?.[w] ? { fill: wardFill[w], fillOpacity: 0.55 } : undefined}
          />
        ))}
        {NET.links.map((l) => {
          const a = NET.nodes[l.a];
          const b = NET.nodes[l.b];
          return <line key={l.id} x1={sx(a.x)} y1={sy(a.y)} x2={sx(b.x)} y2={sy(b.y)} className={`pipe ${l.type}`} />;
        })}
        {Object.entries(NET.nodes).map(([id, n]) => {
          const ratio = ratios?.[id];
          if (n.type !== "Junction")
            return (
              <rect key={id} x={sx(n.x) - r} y={sy(n.y) - r} width={2 * r} height={2 * r} className="source">
                <title>{`${n.type} ${id} (water source)`}</title>
              </rect>
            );
          if (ratio === undefined) return <circle key={id} cx={sx(n.x)} cy={sy(n.y)} r={r * 0.35} className="nodemand" />;
          const dry = ratio < dryThreshold;
          return (
            <g key={id}>
              <circle cx={sx(n.x)} cy={sy(n.y)} r={r} fill={ratioColor(ratio)} className="junction">
                <title>{`Junction ${id} · ${junctionWard[id] ?? ""} · ${Math.round(ratio * 100)}% of demand served${dry ? " (dry)" : ""}`}</title>
              </circle>
              {dry && (
                <path
                  d={`M${sx(n.x) - r * 1.5},${sy(n.y) - r * 1.5} L${sx(n.x) + r * 1.5},${sy(n.y) + r * 1.5} M${sx(n.x) - r * 1.5},${sy(n.y) + r * 1.5} L${sx(n.x) + r * 1.5},${sy(n.y) - r * 1.5}`}
                  className="dry-x"
                />
              )}
            </g>
          );
        })}
        {markers.map((m) => {
          const n = NET.nodes[m.node];
          return (
            <g key={m.node} className="leak-marker">
              <circle cx={sx(n.x)} cy={sy(n.y)} r={r * 2.2} />
              {/* label on the left when the marker is near the right edge, so it is never clipped */}
              <text x={sx(n.x) > W * 0.8 ? sx(n.x) - r * 2.6 : sx(n.x) + r * 2.6} y={sy(n.y) + r} fontSize={r * 2.4}
                textAnchor={sx(n.x) > W * 0.8 ? "end" : "start"}>{m.label}</text>
            </g>
          );
        })}
        {Object.entries(NET.wards).map(([w, ward]) => (
          <text key={w} x={sx(ward.label[0])} y={sy(ward.label[1])} className="ward-label" fontSize={r * 3}>
            {w}
            {wardNote?.[w] && <tspan x={sx(ward.label[0])} dy={r * 3.2} fontSize={r * 2.5}>{wardNote[w]}</tspan>}
          </text>
        ))}
      </svg>
      {ratios && (
        <div className="legend" aria-hidden>
          <span>0%</span>
          <span className="gradient" style={{ background: `linear-gradient(90deg, ${RDYLGN.join(",")})` }} />
          <span>100% of demand served</span>
          <span className="legend-x">✕ dry (&lt;{Math.round(dryThreshold * 100)}%)</span>
          <span className="legend-src">■ source / tank</span>
        </div>
      )}
    </figure>
  );
}
