import { useEffect, useState } from "react";
import { useApi, type CityScenarios, type PlanLatest } from "../data";
import { Bi, T } from "../i18n";
import { fmtKL } from "../ui";

const CUT_STARTS = new Date("2026-10-16T00:00:00+05:30"); // midnight IST
const DAY_MS = 86_400_000;

/** Before 16 Oct 2026 (IST): countdown. From 16 Oct: "in force since 16 Oct 2026 · day N" (16 Oct = day 1). */
function Countdown() {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  const ms = CUT_STARTS.getTime() - now;
  if (ms <= 0) {
    const day = Math.floor(-ms / DAY_MS) + 1;
    return (
      <div className="countdown in-force" data-testid="cut-in-force">
        <div className="cd-part"><b>Day {day}</b><span>of the cut</span></div>
        <div className="cd-caption">
          <Bi t={T.inForce} /> since 16 Oct 2026 · day {day}
        </div>
      </div>
    );
  }
  const s = Math.floor(ms / 1000);
  const parts = [
    [Math.floor(s / 86400), "days"],
    [Math.floor((s % 86400) / 3600), "hours"],
    [Math.floor((s % 3600) / 60), "min"],
    [s % 60, "sec"],
  ] as const;
  return (
    <div className="countdown" aria-live="off" data-testid="cut-countdown">
      {parts.map(([v, u]) => (
        <div key={u} className="cd-part"><b>{String(v).padStart(2, "0")}</b><span>{u}</span></div>
      ))}
      <div className="cd-caption"><Bi t={T.countdown} /></div>
    </div>
  );
}

export default function Home() {
  const city = useApi<CityScenarios>("/city/scenarios");
  const village = useApi<PlanLatest>("/village/plan/latest");
  const blunt = city.data?.scenarios.BLUNT;
  const fair = city.data?.scenarios.FAIR;
  const one = village.data?.views["Tuljapur"]?.comparison;
  const two = village.data?.views["Tuljapur+Naldurg"]?.comparison.find((c) => !c.plan.startsWith("naive"));
  const naive = one?.find((c) => c.plan.startsWith("naive"));
  const opt = one?.find((c) => !c.plan.startsWith("naive"));

  return (
    <div className="page home">
      <section className="hero">
        <p className="kicker">Maharashtra, October 2026</p>
        <h1>
          <span className="hero-num">265</span> of 358 talukas are in drought.
          <br />A mandatory <span className="hero-num">10%</span> water cut{" "}
          {Date.now() >= CUT_STARTS.getTime() ? "has been in force since 16 Oct 2026." : "starts 16 Oct 2026."}
        </h1>
        <Countdown />
        <p className="sources">
          Sources:{" "}
          <a href="https://theprint.in/india/tackling-drought-minimum-10-pc-water-cut-in-maharashtra-urban-rural-local-bodies-from-oct-16/3062260/" target="_blank" rel="noreferrer">PTI via ThePrint: 10% cut from 16 Oct</a>
          {" · "}
          <a href="https://www.awazthevoice.in/india-news/maharashtra-govt-declares-of-talukas-drought-affected-68425.html" target="_blank" rel="noreferrer">PTI: 265 of 358 talukas declared drought-affected</a>
          {" · "}
          <a href="https://newsonair.gov.in/central-teams-to-visit-karnataka-and-maharashtra-to-assess-drought-situation-2/" target="_blank" rel="noreferrer">AIR: drought declared, central teams to visit</a>
        </p>
        <p className="lead">JalNyay (जलन्याय, "water justice") helps officials share the shortage fairly: a fair cut in cities, and smarter tankers for villages.</p>
      </section>

      <section className="cards2">
        <a className="bigcard" href="#/city">
          <Bi t={T.city} as="h2" />
          {fair && blunt ? (
            <>
              <p className="headline"><b>{fair.dry_count}</b> areas run dry instead of <b>{blunt.dry_count}</b></p>
              <p>Same {fair.reduction_pct.toFixed(0)}% saving. Under-served areas {blunt.underserved_count} → {fair.underserved_count}; the worst-off area gets {Math.round(fair.min_ratio * 100)}% of its water instead of {Math.round(blunt.min_ratio * 100)}%.</p>
            </>
          ) : <p className="muted">{city.error ? "Could not load results." : "Loading results…"}</p>}
          <p className="muted small">For a municipal water engineer · sample network, simulated wards</p>
          <span className="btn primary"><Bi t={T.open} /> →</span>
        </a>
        <a className="bigcard" href="#/village">
          <Bi t={T.village} as="h2" />
          {naive && opt ? (
            <>
              <p className="headline">
                Same {village.data?.views["Tuljapur"].params.n_tankers} tankers: first come, first served leaves <b>{naive.high_need_unserved} of {naive.high_need_villages}</b> high-need
                villages dry. JalNyay leaves <b>{opt.high_need_unserved}</b>.
              </p>
              {two && <p>Adding Naldurg as a second filling point: {fmtKL(opt.litres_delivered)} → {fmtKL(two.litres_delivered)} a day (+{Math.round((two.litres_delivered / opt.litres_delivered - 1) * 100)}%).</p>}
            </>
          ) : <p className="muted">{village.error ? "Could not load results." : "Loading results…"}</p>}
          <p className="muted small">For a tehsildar / Zilla Parishad officer · Tuljapur taluka, Dharashiv</p>
          <span className="btn primary"><Bi t={T.open} /> →</span>
        </a>
      </section>
    </div>
  );
}
