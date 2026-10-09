import { useEffect, useMemo, useState } from "react";
import { runJob, warmUpCity, type JobProgress } from "../api";
import { useApi, type CityEvalResult, type CityMetrics, type CityScenarios } from "../data";
import { Bi, T } from "../i18n";
import NetworkMap, { WARD_NAMES } from "../components/NetworkMap";
import { ErrorBox, JobStatus, Loading, Metric, SimNote, pct } from "../ui";

const LABEL = "Representative sample network (EPA Net3), SIMULATED wards and leaks";

// Valve throttle slider: 0 = fully open; otherwise K = 10^(s/25 - 0.5), i.e. ~0.3 .. 3,000 (log scale).
const toK = (s: number) => (s <= 0 ? 0 : Math.round(10 ** (s / 25 - 0.5) * 100) / 100);
const toS = (k: number) => (k <= 0 ? 0 : Math.max(1, Math.min(100, Math.round((Math.log10(k) + 0.5) * 25))));
const throttleWord = (k: number) => (k < 1 ? "open" : k < 50 ? "light" : k < 500 ? "medium" : "strong");

function MetricRow({ m, vs }: { m: CityMetrics; vs?: CityMetrics }) {
  const delta = (a: number, b?: number) => (b === undefined ? undefined : a - b);
  const dDry = delta(m.dry_count, vs?.dry_count);
  const dUnder = delta(m.underserved_count, vs?.underserved_count);
  return (
    <div className="metrics">
      <Metric label="water saved" value={`${m.reduction_pct.toFixed(1)}%`} sub="target 9.5-10.5%" />
      <Metric label="dry junctions (<50%)" value={m.dry_count} tone={m.dry_count ? "bad" : "good"}
        sub={dDry !== undefined && dDry !== 0 ? `${dDry > 0 ? "+" : ""}${dDry} vs blunt` : undefined} />
      <Metric label="under-served (<80%)" value={m.underserved_count}
        sub={dUnder !== undefined && dUnder !== 0 ? `${dUnder > 0 ? "+" : ""}${dUnder} vs blunt` : undefined} />
      <Metric label="worst-off junction gets" value={pct(m.min_ratio)} tone={m.min_ratio < 0.5 ? "bad" : undefined} />
      <Metric label="average (demand-weighted)" value={pct(m.weighted_mean_ratio, 1)} />
    </div>
  );
}

export default function City() {
  const { data, error, loading, retry } = useApi<CityScenarios>("/city/scenarios");
  const [settings, setSettings] = useState<Record<string, number>>({});
  const [progress, setProgress] = useState<JobProgress | null>(null);
  const [mine, setMine] = useState<CityEvalResult | null>(null);
  const [jobError, setJobError] = useState<unknown>(null);

  useEffect(() => warmUpCity(), []); // start a Lambda container now so "Try your own plan" is fast
  useEffect(() => {
    if (data && !Object.keys(settings).length) setSettings({ ...data.scenarios.FAIR.ward_settings });
  }, [data, settings]);

  const leakFill = useMemo(() => {
    if (!data) return undefined;
    const fill: Record<string, string> = {};
    for (const [w, v] of Object.entries(data.leaks.per_ward)) {
      const t = Math.min(1, Math.max(0, v.anomaly_pct) / 50);
      fill[w] = `rgba(196, 30, 58, ${0.08 + 0.6 * t})`;
    }
    return fill;
  }, [data]);

  if (loading) return <Loading what="city scenarios" />;
  if (error || !data) return <ErrorBox error={error} onRetry={retry} />;
  const { BLUNT, FAIR } = data.scenarios;

  async function simulate() {
    setJobError(null);
    setMine(null);
    try {
      const out = await runJob<CityEvalResult>("city_evaluate", { plan: "custom", ward_settings: settings }, setProgress);
      setMine(out.result);
    } catch (e) {
      setJobError(e);
    } finally {
      setProgress(null);
    }
  }

  const band = mine ? mine.metrics.reduction_pct : 0;
  const bandMsg = !mine
    ? ""
    : mine.in_target_band
      ? "Within the 9.5-10.5% target."
      : band < 9.5
        ? "Saves less than the 10% target: tighten some valves."
        : "Saves more than needed: open some valves.";

  return (
    <div className="page">
      <header className="page-head">
        <Bi t={T.city} as="h1" />
        <p className="lead">
          Same 10% saving, different fairness. A <b>blunt</b> cut throttles both source mains equally; the <b>fair</b> plan
          sets each ward's inlet valve separately so no junction runs dry.
        </p>
        <SimNote>{LABEL}. 7-day pressure-driven simulation (WNTR), last 24 h scored.</SimNote>
      </header>

      <section className="grid2">
        <div className="card">
          <Bi t={T.blunt} as="h2" />
          <MetricRow m={BLUNT} />
          <NetworkMap title="Blunt cut: same throttle on both source mains" ratios={BLUNT.junction_ratio} ariaLabel="Network map, blunt cut" />
        </div>
        <div className="card">
          <Bi t={T.fair} as="h2" />
          <MetricRow m={FAIR} vs={BLUNT} />
          <NetworkMap title="Fair cut: per-ward inlet valves" ratios={FAIR.junction_ratio} ariaLabel="Network map, fair cut" />
        </div>
      </section>

      <section className="card">
        <Bi t={T.wards} as="h2" />
        <div className="table-wrap">
          <table>
            <thead>
              <tr><th>Ward (simulated)</th><th>Blunt: mean served</th><th>Fair: mean served</th><th>Change</th><th>Fair valve setting (K)</th></tr>
            </thead>
            <tbody>
              {WARD_NAMES.map((w) => {
                const b = BLUNT.ward_mean_ratio[w];
                const f = FAIR.ward_mean_ratio[w];
                const k = FAIR.ward_settings?.[w] ?? 0;
                return (
                  <tr key={w}>
                    <td>{w}</td><td>{pct(b)}</td><td>{pct(f)}</td>
                    <td className={f - b >= 0 ? "pos" : "neg"}>{f - b >= 0 ? "+" : ""}{((f - b) * 100).toFixed(1)} pts</td>
                    <td>{k.toLocaleString("en-IN", { maximumFractionDigits: 1 })} ({throttleWord(k)})</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <p className="muted small">K is the valve's loss coefficient: higher = more throttled. Wards C, D and E stay open in the fair plan.</p>
      </section>

      <section className="card">
        <Bi t={T.tryPlan} as="h2" />
        <p className="muted">Set each ward's inlet valve, then run the same 7-day simulation on AWS Lambda. Starts from the fair plan.</p>
        <div className="sliders">
          {WARD_NAMES.map((w) => {
            const k = settings[w] ?? 0;
            return (
              <label key={w} className="slider">
                <span className="slider-name">{w}</span>
                <input type="range" min={0} max={100} value={toS(k)} disabled={!!progress}
                  onChange={(e) => setSettings({ ...settings, [w]: toK(Number(e.target.value)) })}
                  aria-label={`${w} inlet valve throttle`} />
                <span className="slider-val">K {k.toLocaleString("en-IN", { maximumFractionDigits: 1 })} · {throttleWord(k)}</span>
              </label>
            );
          })}
        </div>
        <div className="actions">
          <button className="btn primary" onClick={simulate} disabled={!!progress}><Bi t={T.runPlan} /></button>
          <button className="btn" disabled={!!progress} onClick={() => setSettings({ ...FAIR.ward_settings })}>Reset to fair plan</button>
          <button className="btn" disabled={!!progress} onClick={() => setSettings(Object.fromEntries(WARD_NAMES.map((w) => [w, 400])))}>Same valve on every ward</button>
        </div>
        {progress && (
          <JobStatus progress={progress} label="Simulating 7 days of the network on AWS Lambda…"
            coldHint="Warming up the simulator on AWS Lambda… the first run after a quiet period takes about 30 s." />
        )}
        {jobError ? <ErrorBox error={jobError} onRetry={simulate} /> : null}
        {mine && (
          <div className="result">
            <p className={mine.in_target_band ? "ok-line" : "warn-line"}>
              Your plan saves <b>{mine.metrics.reduction_pct.toFixed(1)}%</b>. {bandMsg}
            </p>
            <MetricRow m={mine.metrics} vs={BLUNT} />
            <NetworkMap title="Your plan" ratios={mine.junction_ratio} ariaLabel="Network map, your plan" />
          </div>
        )}
      </section>

      <section className="card">
        <Bi t={T.leaks} as="h2" />
        <p className="muted">
          Night-flow check: bulk meters on each ward boundary, 02:00-04:00, compared with a no-leak night. Three leaks were
          injected (simulated); {data.leaks.threshold}.
        </p>
        <div className="grid2 tight">
          <NetworkMap title="Night inflow above normal, by ward" wardFill={leakFill}
            wardNote={Object.fromEntries(Object.entries(data.leaks.per_ward).map(([w, v]) => [w, `${v.anomaly_pct > 0 ? "+" : ""}${v.anomaly_pct.toFixed(0)}%${v.flagged ? " ⚑" : ""}`]))}
            markers={data.leaks.leaks.map((l) => ({ node: l.junction, label: "leak" }))} ariaLabel="Leak anomaly map" />
          <div>
            <div className="table-wrap">
              <table>
                <thead><tr><th>Ward</th><th>Night inflow vs normal</th><th>Flagged</th><th>Leak injected here?</th></tr></thead>
                <tbody>
                  {Object.entries(data.leaks.per_ward).map(([w, v]) => (
                    <tr key={w} className={v.flagged ? "flag-row" : ""}>
                      <td>{w}</td><td>{v.anomaly_pct > 0 ? "+" : ""}{v.anomaly_pct.toFixed(1)}%</td>
                      <td>{v.flagged ? "⚑ yes" : "no"}</td><td>{v.contains_leak ? "yes" : "no"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="note">
              <b>Honest note on Ward F:</b> its leak was missed. Ward F has large always-on users in the sample network, so
              a ~22 L/s leak adds only ~5% to its night inflow, below the 10% threshold. Fix: meter large users separately,
              or split Ward F into smaller metered zones. Found {data.leaks.true_positives.length} of {data.leaks.leaks.length} leaks, no false alarms.
            </p>
          </div>
        </div>
      </section>
    </div>
  );
}
