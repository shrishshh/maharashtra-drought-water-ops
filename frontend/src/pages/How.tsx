import { Bi, T } from "../i18n";

const GITHUB = "https://github.com/shrishshh/maharashtra-drought-water-ops";

// Box helper for the architecture diagram (inline SVG, site palette).
function Box({ x, y, w, h, title, lines, accent }: { x: number; y: number; w: number; h: number; title: string; lines: string[]; accent?: boolean }) {
  return (
    <g>
      <rect x={x} y={y} width={w} height={h} rx={10} className={accent ? "arch-box accent" : "arch-box"} />
      <text x={x + 12} y={y + 22} className="arch-title">{title}</text>
      {lines.map((l, i) => (
        <text key={l} x={x + 12} y={y + 42 + i * 17} className="arch-line">{l}</text>
      ))}
    </g>
  );
}

function Arrow({ d, label, lx, ly, dashed }: { d: string; label?: string; lx?: number; ly?: number; dashed?: boolean }) {
  return (
    <g>
      <path d={d} className={dashed ? "arch-arrow dashed" : "arch-arrow"} markerEnd="url(#arrowhead)" />
      {label && <text x={lx} y={ly} className="arch-label">{label}</text>}
    </g>
  );
}

function Diagram() {
  return (
    <div className="arch-wrap">
      <svg viewBox="0 0 1000 470" role="img" aria-labelledby="arch-title arch-desc" className="arch">
        <title id="arch-title">JalNyay architecture on AWS</title>
        <desc id="arch-desc">
          The browser loads the site from AWS Amplify Hosting and calls Amazon API Gateway. An API Lambda function validates
          requests, stores job rows in DynamoDB and inputs in S3, and starts worker Lambda functions asynchronously. Workers
          (city simulation, village planning, fleet sweep, fraud check) run as container images from Amazon ECR, write results
          to S3 and status to DynamoDB, and log to CloudWatch. Road distances come from Amazon Location Service, computed once
          and cached in S3 for at most 30 days.
        </desc>
        <defs>
          <marker id="arrowhead" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M0,0 L10,5 L0,10 z" className="arch-head" />
          </marker>
        </defs>

        <Box x={20} y={30} w={180} h={66} title="Browser" lines={["laptop or phone"]} />
        <Box x={20} y={170} w={180} h={84} title="AWS Amplify Hosting" lines={["React site (static)", "this page"]} accent />
        <Box x={260} y={30} w={200} h={84} title="Amazon API Gateway" lines={["HTTP API, CORS: site only", "5 req/s, burst 10"]} accent />
        <Box x={520} y={30} w={190} h={84} title="API Lambda" lines={["validate, create job,", "read-only endpoints"]} accent />
        <g>
          <rect x={520} y={170} width={190} height={250} rx={10} className="arch-box group" />
          <text x={532} y={192} className="arch-title">Worker Lambdas</text>
          <text x={532} y={210} className="arch-line">container images</text>
          {["city_evaluate · WNTR", "village_plan · OR-Tools", "village_fleet · sweep", "village_fraud · GPS"].map((t, i) => (
            <g key={t}>
              <rect x={534} y={224 + i * 47} width={162} height={38} rx={7} className="arch-box worker" />
              <text x={546} y={248 + i * 47} className="arch-line strong">{t}</text>
            </g>
          ))}
        </g>
        <rect x={790} y={14} width={196} height={276} rx={12} className="arch-box group" />
        <text x={802} y={34} className="arch-line">data</text>
        <Box x={803} y={44} w={170} h={84} title="Amazon DynamoDB" lines={["job status (2-day TTL)", "villages + need"]} accent />
        <Box x={803} y={170} w={170} h={102} title="Amazon S3" lines={["precomputed results", "job inputs + results", "road matrix (≤30 days)"]} accent />
        <Box x={803} y={340} w={170} h={80} title="Amazon Location" lines={["road distances + times", "computed once"]} accent />
        <Box x={260} y={300} w={200} h={66} title="Amazon ECR" lines={["worker images"]} accent />
        <Box x={260} y={390} w={200} h={60} title="Amazon CloudWatch" lines={["logs, 7 days"]} accent />

        <Arrow d="M110,170 L110,100" label="loads site" lx={118} ly={140} />
        <Arrow d="M200,63 L256,63" label="HTTPS" lx={206} ly={55} />
        <Arrow d="M460,72 L516,72" />
        <Arrow d="M710,72 L786,72" label="job row," lx={716} ly={60} />
        <text x={716} y={92} className="arch-label">input</text>
        <Arrow d="M615,114 L615,166" label="async invoke" lx={622} ly={146} />
        <Arrow d="M710,230 L786,230" label="status," lx={716} ly={218} />
        <text x={716} y={250} className="arch-label">results</text>
        <Arrow d="M888,340 L888,294" label="cached" lx={895} ly={322} />
        <Arrow d="M460,333 L516,333" dashed label="image" lx={466} ly={325} />
        <Arrow d="M516,410 L464,418" dashed label="logs" lx={474} ly={404} />
      </svg>
    </div>
  );
}

const SERVICES: [string, string][] = [
  ["AWS Amplify Hosting", "Serves this website (static React build, deployed as a zip)."],
  ["Amazon API Gateway (HTTP API)", "Public API; accepts calls only from this site (CORS) and throttles to 5 requests/s."],
  ["AWS Lambda (API)", "Validates requests, records each job, starts the right worker, serves precomputed results."],
  ["AWS Lambda (workers, container images)", "Run the engines: water-network simulation (WNTR), tanker routing (OR-Tools), fleet sweep, fraud checks. Simulations take 3-30 s, longer than an API call allows, so they run as background jobs the page polls every 4 s."],
  ["Amazon DynamoDB", "Job status (deleted after 2 days) and the Tuljapur villages with their need scores."],
  ["Amazon S3", "Private, encrypted store for precomputed results, job inputs/results (deleted after 7 days) and the cached road matrix."],
  ["Amazon Location Service", "Real road distances and drive times between the 32 villages and 2 filling points, computed once and cached for at most 30 days (AWS Service Terms)."],
  ["Amazon ECR", "Holds the worker container images (the engines are too large for a plain Lambda zip)."],
  ["Amazon CloudWatch", "Logs for every function, kept 7 days."],
];

export default function How() {
  return (
    <div className="page">
      <header className="page-head">
        <Bi t={T.how} as="h1" />
        <p className="lead">Two engines, one serverless backend on AWS (region Mumbai, ap-south-1).</p>
      </header>

      <section className="card">
        <h2>Architecture</h2>
        <Diagram />
      </section>

      <section className="card">
        <h2>Which AWS service does what</h2>
        <div className="table-wrap">
          <table className="wrap-cells">
            <thead><tr><th>Service</th><th>Job in JalNyay</th></tr></thead>
            <tbody>{SERVICES.map(([s, j]) => <tr key={s}><td><b>{s}</b></td><td>{j}</td></tr>)}</tbody>
          </table>
        </div>
      </section>

      <section className="grid2">
        <div className="card">
          <Bi t={T.city} as="h2" />
          <ol className="steps">
            <li><b>Simulate the network.</b> 7 days of a water network with pressure-driven demand (WNTR / EPANET): when pressure drops, homes get less water. The last day is scored per junction: water delivered ÷ water demanded.</li>
            <li><b>Blunt cut.</b> Throttle both source mains equally until the city uses 10% less water.</li>
            <li><b>Fair cut.</b> Search 58 plans of per-ward inlet-valve settings, each scaled to the same 10% saving; keep the one with the fewest under-served areas, then the best-off worst area.</li>
            <li><b>Leak zones.</b> Compare each ward's night inflow (02:00-04:00 bulk meters) with a normal night; flag wards more than 10% above.</li>
          </ol>
        </div>
        <div className="card">
          <Bi t={T.village} as="h2" />
          <ol className="steps">
            <li><b>Need score.</b> People × 20 L/day + livestock (35 L large, 10 L small), multiplied by urgency (source dry, days since the last tanker). Only villages whose own source is dry are eligible.</li>
            <li><b>Road distances.</b> Drive times and distances between villages and filling points from Amazon Location Service.</li>
            <li><b>Route tankers.</b> OR-Tools plans a 10-hour day per tanker: fill, deliver, refill, repeat. Skipping a village costs its need score, so the neediest are served first.</li>
            <li><b>Check trips.</b> Each claimed delivery is checked against GPS: a visit within 300 m, a long enough stop, a refill since the last load, a plausible speed.</li>
          </ol>
        </div>
      </section>

      <section className="card">
        <h2>Limitations and what is simulated</h2>
        <ul className="steps">
          <li><b>City:</b> runs on EPA's Net3 <i>sample</i> network, not a real Maharashtra network. Wards, leaks and meter noise are simulated. A real deployment needs the city's own EPANET model.</li>
          <li><b>Village:</b> village locations (OpenStreetMap) and population (Census 2011) are real; livestock, source status, requests, tanker history, GPS traces and trip claims are simulated.</li>
          <li><b>Assumptions to verify:</b> water norms (20 L/person/day; livestock 35 / 10 L) against the Maharashtra drought GR; Tuljapur and Naldurg as filling points.</li>
          <li><b>Heuristics:</b> OR-Tools runs with a time limit, so plans are very good, not proven optimal. Fleet thresholds are upper bounds.</li>
          <li><b>Fraud checks</b> were tested on simulated GPS; with long signal gaps they raise false alarms, so a flag means "review", not proof.</li>
        </ul>
        <p>
          Source code, data notes and tests: <a href={GITHUB} target="_blank" rel="noreferrer">github.com/shrishshh/maharashtra-drought-water-ops</a>
        </p>
      </section>
    </div>
  );
}
