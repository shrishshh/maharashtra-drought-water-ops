import { useEffect, useMemo, useState } from "react";
import { runJob, type JobProgress } from "../api";
import { useApi, type PlanLatest, type PlanResult, type Village } from "../data";
import { Bi, T } from "../i18n";
import VillageMap, { type TraceOverlay } from "../components/VillageMap";
import FleetChart from "../components/FleetChart";
import staticData from "../data/village_static.json";
import { ErrorBox, JobStatus, Loading, Metric, SimNote, fmtInt, fmtKL } from "../ui";

const LABEL = "Real village locations (OSM) + Census 2011 population; livestock, requests, trips and GPS SIMULATED";
const FILL_OPTIONS = [
  { key: "Tuljapur", label: "Tuljapur only", mr: "फक्त तुळजापूर", points: ["Tuljapur"] },
  { key: "Tuljapur+Naldurg", label: "Tuljapur + Naldurg", mr: "तुळजापूर + नळदुर्ग", points: ["Tuljapur", "Naldurg"] },
];
interface FraudReport {
  n_claims: number;
  flagged: { claim_id: string; tanker: string; village: string; claimed_time: string; reasons: string[] }[];
}
const FRAUD = staticData.fraud_demo as unknown as {
  label: string;
  claims: { claim_id: string; tanker: string; village_id: string; litres: number; claimed_min: number; injected_fraud: string | null }[];
  traces: Record<string, [number, number, number][]>;
  report: FraudReport;
  evaluation: { precision: number; recall: number; injected: number };
};

/** Precision / recall of a report against the injected (simulated) frauds. */
function scoreReport(report: FraudReport) {
  const injected = new Set(FRAUD.claims.filter((c) => c.injected_fraud).map((c) => c.claim_id));
  const flagged = new Set(report.flagged.map((f) => f.claim_id));
  const tp = [...flagged].filter((id) => injected.has(id)).length;
  return { flagged: flagged.size, injected: injected.size, precision: flagged.size ? tp / flagged.size : 0, recall: injected.size ? tp / injected.size : 0 };
}
const FILL_POINTS = staticData.fill_points;

const cfgKey = (n: number, fp: string) => `${n}|${fp}`;

export default function VillagePage() {
  const villagesQ = useApi<{ villages: Village[] }>("/village/villages");
  const latestQ = useApi<PlanLatest>("/village/plan/latest");
  const [n, setN] = useState(6);
  const [fp, setFp] = useState("Tuljapur");
  const [results, setResults] = useState<Record<string, PlanResult & { live?: string }>>({});
  const [progress, setProgress] = useState<JobProgress | null>(null);
  const [jobError, setJobError] = useState<unknown>(null);
  const [claimId, setClaimId] = useState<string | null>(null);
  const [liveFraud, setLiveFraud] = useState<{ report: FraudReport; elapsedS: number } | null>(null);
  const [fraudProgress, setFraudProgress] = useState<JobProgress | null>(null);
  const [fraudError, setFraudError] = useState<unknown>(null);

  useEffect(() => {
    if (!latestQ.data) return;
    const seeded: Record<string, PlanResult> = {};
    for (const [k, v] of Object.entries(latestQ.data.views)) seeded[cfgKey(v.params.n_tankers, k)] = v;
    setResults((r) => ({ ...seeded, ...r }));
  }, [latestQ.data]);

  const current = results[cfgKey(n, fp)];
  const shown = current ?? results[cfgKey(6, fp)];
  const villages = villagesQ.data?.villages ?? [];
  const byId = useMemo(() => Object.fromEntries(villages.map((v) => [v.village_id, v])), [villages]);

  const trace: TraceOverlay | null = useMemo(() => {
    const c = FRAUD.claims.find((x) => x.claim_id === claimId);
    if (!c || !byId[c.village_id]) return null;
    const pts = (FRAUD.traces[c.tanker] ?? []).filter((p) => Math.abs(p[0] - c.claimed_min) <= 60).map((p) => [p[1], p[2]] as [number, number]);
    const f = (liveFraud?.report ?? FRAUD.report).flagged.find((x) => x.claim_id === claimId);
    return { points: pts, claimed: byId[c.village_id], radiusM: 300, label: `${c.claim_id} claimed ${fmtInt(c.litres)} L at ${byId[c.village_id].name}, ${f?.claimed_time ?? ""}` };
  }, [claimId, byId, liveFraud]);

  if (villagesQ.loading || latestQ.loading) return <Loading what="Tuljapur villages and the default plan" />;
  if (villagesQ.error) return <ErrorBox error={villagesQ.error} onRetry={villagesQ.retry} />;
  if (latestQ.error || !latestQ.data) return <ErrorBox error={latestQ.error} onRetry={latestQ.retry} />;
  const latest = latestQ.data;

  async function plan() {
    setJobError(null);
    const fillPoints = FILL_OPTIONS.find((o) => o.key === fp)!.points;
    try {
      const out = await runJob<PlanResult>("village_plan", { n_tankers: n, filling_points: fillPoints }, setProgress);
      setResults((r) => ({ ...r, [cfgKey(n, fp)]: { ...out.result, live: `live from AWS Lambda in ${out.elapsedS} s` } }));
    } catch (e) {
      setJobError(e);
    } finally {
      setProgress(null);
    }
  }

  async function rerunFraud() {
    setFraudError(null);
    try {
      // the detector never sees the simulated ground truth: strip injected_fraud before sending
      const claims = FRAUD.claims.map(({ injected_fraud: _ignored, ...c }) => c);
      const out = await runJob<{ report: FraudReport }>("village_fraud", { claims, traces: FRAUD.traces }, setFraudProgress);
      setLiveFraud({ report: out.result.report, elapsedS: out.elapsedS });
    } catch (e) {
      setFraudError(e);
    } finally {
      setFraudProgress(null);
    }
  }

  // Compare like with like: first come, first served is only modelled from the Tuljapur base, so with
  // "Tuljapur + Naldurg" selected the comparison uses the Tuljapur-only pair (same tanker count if planned).
  const cmpSource = fp === "Tuljapur" ? shown : results[cfgKey(n, "Tuljapur")] ?? results[cfgKey(6, "Tuljapur")];
  const cmpTankers = cmpSource?.params.n_tankers ?? 6;
  const naive = cmpSource?.comparison.find((c) => c.plan.startsWith("naive"));
  const opt = cmpSource?.comparison.find((c) => !c.plan.startsWith("naive"));
  const fraudReport = liveFraud?.report ?? FRAUD.report;
  const liveScore = liveFraud ? scoreReport(liveFraud.report) : null;
  const sameAsBundled = liveFraud
    ? JSON.stringify(liveFraud.report.flagged.map((f) => [f.claim_id, f.reasons])) === JSON.stringify(FRAUD.report.flagged.map((f) => [f.claim_id, f.reasons]))
    : false;
  const one = latest.views["Tuljapur"]?.comparison.find((c) => !c.plan.startsWith("naive"));
  const two = latest.views["Tuljapur+Naldurg"]?.comparison.find((c) => !c.plan.startsWith("naive"));
  const fillFor = (key: string) => FILL_POINTS.filter((f) => FILL_OPTIONS.find((o) => o.key === key)!.points.includes(f.name));
  const servedToday = shown?.plans.optimised.litres_by_village ?? {};

  return (
    <div className="page">
      <header className="page-head">
        <Bi t={T.village} as="h1" />
        <p className="lead">
          Tuljapur taluka, Dharashiv. {latest.views["Tuljapur"] ? `${fmtInt(villages.filter((v) => v.eligible).length)} villages` : "Villages"} whose own
          source has run dry need tankers. The planner sends each tanker where the need is greatest, using road distances.
        </p>
        <SimNote>{LABEL}. Only villages whose source is dry (simulated) are eligible.</SimNote>
      </header>

      <section className="card">
        <div className="controls">
          <label className="control">
            <Bi t={T.tankers} />
            <input type="range" min={1} max={40} value={n} disabled={!!progress} onChange={(e) => setN(Number(e.target.value))} aria-label="Number of tankers" />
            <b className="big">{n}</b>
          </label>
          <div className="control" role="radiogroup" aria-label="Filling points">
            <Bi t={T.fillPoints} />
            <div className="seg">
              {FILL_OPTIONS.map((o) => (
                <button key={o.key} role="radio" aria-checked={fp === o.key} className={fp === o.key ? "on" : ""} disabled={!!progress} onClick={() => setFp(o.key)}>
                  {o.label} <span className="mr" lang="mr">{o.mr}</span>
                </button>
              ))}
            </div>
          </div>
          <button className="btn primary" onClick={plan} disabled={!!progress || !!current}>
            {current ? "✓ Plan shown below" : <Bi t={T.planTankers} />}
          </button>
        </div>
        <p className="muted small">
          {current
            ? `Showing ${current.live ?? "the precomputed default plan"} · ${current.distance_provider.startsWith("cached") ? "road distances (Amazon Location)" : current.distance_provider}.`
            : `Not planned yet for ${n} tankers: press the button (about 20-30 s on AWS). Map below still shows ${n === 6 ? "" : "the 6-tanker "}default plan.`}
        </p>
        {progress && <JobStatus progress={progress} label="Planning routes with OR-Tools on AWS Lambda…" coldHint="Starting a fresh Lambda container… the solver itself runs for 20 s." />}
        {jobError ? <ErrorBox error={jobError} onRetry={plan} /> : null}

        <VillageMap villages={villages} fillPoints={fillFor(fp)} plan={shown?.plans.optimised} />
        <p className="muted small map-note">
          Lines show stop order; distances and travel times use real roads (Amazon Location Service).
        </p>
      </section>

      {naive && opt && (
        <section className="card">
          <Bi t={T.compare} as="h2" />
          <p className="cmp-context">
            Same <b>{cmpTankers} tankers</b>, same filling point (<b>Tuljapur</b>) for both: the only difference is how stops are chosen.
            {fp !== "Tuljapur" && " (Shown for Tuljapur because first come, first served is modelled from one filling point only.)"}
          </p>
          <div className="compare">
            {[
              { k: "Litres delivered today", a: fmtKL(naive.litres_delivered), b: fmtKL(opt.litres_delivered) },
              { k: "Litres per km driven", a: fmtInt(naive.litres_per_km), b: fmtInt(opt.litres_per_km) },
              { k: "Villages that got water", a: `${naive.villages_served}`, b: `${opt.villages_served}` },
              { k: `High-need villages left dry (of ${opt.high_need_villages})`, a: `${naive.high_need_unserved}`, b: `${opt.high_need_unserved}`, good: opt.high_need_unserved === 0 },
            ].map((row) => (
              <div key={row.k} className="cmp-row">
                <div className="cmp-label">{row.k}</div>
                <div className="cmp-a"><span className="tag">first come, first served</span>{row.a}</div>
                <div className={`cmp-b ${row.good ? "good" : ""}`}><span className="tag">JalNyay (optimised)</span>{row.b}</div>
              </div>
            ))}
          </div>
        </section>
      )}

      {one && two && (
        <section className="card">
          <h2>Add Naldurg as a second filling point <span className="mr" lang="mr">नळदुर्ग येथे दुसरे भरणा केंद्र</span></h2>
          <p className="headline-line">
            {fmtKL(one.litres_delivered)} → <b>{fmtKL(two.litres_delivered)}</b> a day
            (+{Math.round((two.litres_delivered / one.litres_delivered - 1) * 100)}%), {Math.round((1 - two.total_km / one.total_km) * 100)}% fewer km
          </p>
          <p className="cmp-context">Same 6 tankers, both plans optimised by JalNyay, road distances. Tankers start and end the day at Tuljapur and may refill at either town.</p>
          <div className="metrics">
            <Metric label="Tuljapur only" value={fmtKL(one.litres_delivered)} sub={`${one.villages_served} villages · ${fmtInt(one.total_km)} km`} />
            <Metric label="Tuljapur + Naldurg" value={fmtKL(two.litres_delivered)} sub={`${two.villages_served} villages · ${fmtInt(two.total_km)} km`} tone="good" />
          </div>
        </section>
      )}

      <section className="card">
        <Bi t={T.fleet} as="h2" />
        <div className="metrics">
          <Metric label="tankers so every high-need village gets a load" value={latest.fleet.min_tankers_every_high_need_one_load ?? "-"} />
          <Metric label="tankers for full drinking water (20 L/person/day)" value={latest.fleet.min_tankers_full_human_need ?? "> 40"} />
        </div>
        <FleetChart fleet={latest.fleet} />
        <p className="muted small">Each point is an OR-Tools plan with a {latest.fleet.solver_time_limit_s_per_point} s limit (road distances, Tuljapur filling point), so these are upper bounds.</p>
      </section>

      <section className="card">
        <Bi t={T.fraud} as="h2" />
        <SimNote>{FRAUD.label}. The detector caught {Math.round(FRAUD.evaluation.recall * 100)}% of injected frauds with {Math.round(FRAUD.evaluation.precision * 100)}% precision ({FRAUD.report.n_claims} claims).</SimNote>
        <div className="actions">
          <button className="btn" onClick={rerunFraud} disabled={!!fraudProgress}><Bi t={T.rerunFraud} /></button>
          {liveFraud && <button className="btn" onClick={() => setLiveFraud(null)}>Show the bundled result</button>}
        </div>
        {fraudProgress && <JobStatus progress={fraudProgress} label="Checking all claims against the GPS traces on AWS Lambda…" coldHint="Starting a fresh Lambda container…" />}
        {fraudError ? <ErrorBox error={fraudError} onRetry={rerunFraud} /> : null}
        {liveFraud && liveScore && (
          <p className="ok-line" role="status">
            Checked live on AWS Lambda in {liveFraud.elapsedS} s: {liveScore.flagged} of {liveFraud.report.n_claims} claims flagged
            {sameAsBundled ? ", identical to the bundled result" : ""}. Against the {liveScore.injected} injected frauds:
            precision {Math.round(liveScore.precision * 100)}%, recall {Math.round(liveScore.recall * 100)}%.
          </p>
        )}
        <p className="muted small">Showing: {liveFraud ? "live result from AWS" : "bundled result (computed by the same detector)"}.</p>
        <div className="grid2 tight">
          <ul className="claims">
            {fraudReport.flagged.map((f) => (
              <li key={f.claim_id}>
                <button className={claimId === f.claim_id ? "claim on" : "claim"} onClick={() => setClaimId(f.claim_id)}>
                  <b>{f.claim_id}</b> · tanker {f.tanker} · {f.village} · {f.claimed_time}
                  {f.reasons.map((r) => <span key={r} className="reason">⚑ {r.replace(/^[a-d] /, "")}</span>)}
                </button>
              </li>
            ))}
          </ul>
          <div>
            {trace ? <VillageMap villages={villages} fillPoints={FILL_POINTS} trace={trace} />
              : <div className="state">Select a flagged claim to see its GPS trace against the claimed stop.</div>}
          </div>
        </div>
      </section>

      <section className="card">
        <Bi t={T.villages} as="h2" />
        <div className="table-wrap tall">
          <table>
            <thead>
              <tr><th>#</th><th>Village</th><th>Population (Census 2011)</th><th>Need score</th><th>Source dry (sim.)</th><th>Days since tanker (sim.)</th><th>Today ({fp.replace("+", " + ")})</th></tr>
            </thead>
            <tbody>
              {[...villages].sort((a, b) => a.rank - b.rank).map((v) => (
                <tr key={v.village_id} className={v.eligible ? "" : "muted-row"}>
                  <td>{v.rank}</td>
                  <td>{v.name} {v.name_mr && <span className="mr" lang="mr">{v.name_mr}</span>} {v.high_need && <span className="pill">high need</span>}</td>
                  <td>{fmtInt(v.population)}</td>
                  <td>{fmtInt(v.need_score / 1000)}k</td>
                  <td>{v.source_dry_sim ? "yes" : "no"}</td>
                  <td>{v.days_since_last_tanker_sim}</td>
                  <td>{v.eligible ? (servedToday[v.village_id] ? fmtKL(servedToday[v.village_id]) : "none") : "not eligible"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="muted small">Need score = daily need (20 L/person + livestock) x urgency (source dry, days waiting). Livestock and urgency inputs are SIMULATED.</p>
      </section>
    </div>
  );
}
