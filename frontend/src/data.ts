// Types for API payloads (only the fields the UI reads) + a small fetch hook + colour scales.
import { useCallback, useEffect, useState } from "react";
import { getJson } from "./api";

export interface CityMetrics {
  reduction_pct: number;
  underserved_count: number;
  dry_count: number;
  min_ratio: number;
  weighted_mean_ratio: number;
  ward_mean_ratio: Record<string, number>;
}
export interface CityScenario extends CityMetrics {
  junction_ratio: Record<string, number>;
  throttle_settings: Record<string, number>;
  ward_settings?: Record<string, number>;
}
export interface LeakWard {
  anomaly_pct: number;
  flagged: boolean;
  contains_leak: boolean;
  true_excess_m3: number;
}
export interface CityScenarios {
  label: string;
  scenarios: { BLUNT: CityScenario; FAIR: CityScenario };
  leaks: {
    leaks: { junction: string; ward: string }[];
    per_ward: Record<string, LeakWard>;
    threshold: string;
    true_positives: string[];
    false_positives: string[];
    false_negatives: string[];
  };
  images?: Record<string, string>;
}
export interface CityEvalResult {
  metrics: CityMetrics;
  junction_ratio: Record<string, number>;
  in_target_band: boolean;
  throttle_settings: Record<string, number>;
}

export interface Village {
  village_id: string;
  name: string;
  name_mr?: string | null;
  lat: number;
  lon: number;
  population: number;
  rank: number;
  need_score: number;
  daily_need_l: number;
  loads_needed: number;
  eligible: boolean;
  high_need: boolean;
  source_dry_sim: boolean;
  days_since_last_tanker_sim: number;
  large_animals_sim: number;
  small_animals_sim: number;
}
export interface PlanEvent {
  type: "fill" | "deliver";
  place: string;
  village_id?: string;
  litres?: number;
  arrive_min: number;
}
export interface Route {
  tanker: string;
  events: PlanEvent[];
  trips: number;
  km: number;
  hours: number;
  litres: number;
}
export interface Plan {
  plan: string;
  routes: Route[];
  litres_by_village: Record<string, number>;
}
export interface ComparisonRow {
  plan: string;
  total_km: number;
  tanker_hours: number;
  litres_delivered: number;
  villages_served: number;
  high_need_villages: number;
  high_need_unserved: number;
  need_covered_pct: number;
  litres_per_km: number;
}
export interface PlanResult {
  params: { n_tankers: number; filling_points: string[] };
  fill_points: string[];
  distance_provider: string;
  plans: { naive?: Plan; optimised: Plan };
  comparison: ComparisonRow[];
  source?: string;
}
export interface FleetResult {
  sweep: { n_tankers: number; need_covered_pct: number; human_need_covered_pct: number; high_need_unserved: number }[];
  min_tankers_every_high_need_one_load: number | null;
  min_tankers_full_human_need: number | null;
  solver_time_limit_s_per_point: number;
}
export interface PlanLatest {
  label: string;
  default_view: string;
  views: Record<string, PlanResult>;
  fleet: FleetResult;
  updated_at: string;
}

/** Fetch JSON with loading / error / retry state. */
export function useApi<T>(path: string) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [nonce, setNonce] = useState(0);
  useEffect(() => {
    let live = true;
    setError(null);
    getJson<T>(path)
      .then((d) => live && setData(d))
      .catch((e) => live && setError(e));
    return () => {
      live = false;
    };
  }, [path, nonce]);
  const retry = useCallback(() => setNonce((n) => n + 1), []);
  return { data, error, loading: !data && !error, retry };
}

// ---- colour scales ----
function lerpHex(a: string, b: string, t: number) {
  const pa = [1, 3, 5].map((i) => parseInt(a.slice(i, i + 2), 16));
  const pb = [1, 3, 5].map((i) => parseInt(b.slice(i, i + 2), 16));
  return "#" + pa.map((v, i) => Math.round(v + (pb[i] - v) * t).toString(16).padStart(2, "0")).join("");
}
function ramp(stops: string[], t: number) {
  const x = Math.min(1, Math.max(0, t)) * (stops.length - 1);
  const i = Math.min(stops.length - 2, Math.floor(x));
  return lerpHex(stops[i], stops[i + 1], x - i);
}
/** Service ratio 0..1: same red-yellow-green scale as the Part B maps (RdYlGn). */
export const RDYLGN = ["#a50026", "#f46d43", "#fee08b", "#66bd63", "#006837"];
export const ratioColor = (r: number) => ramp(RDYLGN, r);
/** Need score: one-hue sequential (light -> dark orange-red). */
export const NEED = ["#fde5c8", "#f9b26f", "#eb6834", "#b8381a", "#7a1d0b"];
export const needColor = (t: number) => ramp(NEED, t);
/** Categorical slots in fixed order (validated reference palette); tankers beyond 8 fold to grey. */
export const CATEGORICAL = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"];
export const OTHER_GREY = "#8a8985";
