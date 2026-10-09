// Coverage vs number of tankers, with the two thresholds marked (values read from the data).
import { CartesianGrid, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { CATEGORICAL, type FleetResult } from "../data";

export default function FleetChart({ fleet }: { fleet: FleetResult }) {
  const a = fleet.min_tankers_every_high_need_one_load;
  const b = fleet.min_tankers_full_human_need;
  const rows = fleet.sweep.map((r) => ({ n: r.n_tankers, full: r.need_covered_pct, human: r.human_need_covered_pct }));
  return (
    <div className="chart" role="img" aria-label={`Coverage versus number of tankers. ${a} tankers give every high-need village water; ${b} tankers cover full drinking water.`}>
      <ResponsiveContainer width="100%" height={320}>
        <LineChart data={rows} margin={{ top: 28, right: 24, bottom: 8, left: 0 }}>
          <CartesianGrid stroke="#e7e6e1" vertical={false} />
          <XAxis dataKey="n" type="number" domain={[0, "dataMax"]} tickCount={9} stroke="#8a8985"
            label={{ value: "tankers (10,000 L, 10 h day)", position: "insideBottom", offset: -4, fill: "#52514e", fontSize: 12 }} />
          <YAxis domain={[0, 100]} unit="%" stroke="#8a8985" width={48} />
          <Tooltip formatter={(v) => `${Number(v).toFixed(1)}%`} labelFormatter={(n) => `${n} tankers`} />
          <Legend verticalAlign="top" height={28} />
          {a !== null && <ReferenceLine x={a} stroke="#52514e" strokeDasharray="4 3"
            label={{ value: `${a}: every high-need village gets water`, position: "insideTopLeft", fill: "#0b0b0b", fontSize: 12 }} />}
          {b !== null && <ReferenceLine x={b} stroke="#52514e" strokeDasharray="4 3"
            label={{ value: `${b}: full drinking water`, position: "insideBottomRight", fill: "#0b0b0b", fontSize: 12 }} />}
          <Line dataKey="human" name="drinking water (20 L/person/day)" stroke={CATEGORICAL[1]} strokeWidth={2} dot={{ r: 3 }} />
          <Line dataKey="full" name="full need (people + livestock)" stroke={CATEGORICAL[0]} strokeWidth={2} dot={{ r: 3 }} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
