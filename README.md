# JalNyay (जलन्याय): fair water in drought

**Live site: https://main.dwnal659uai38.amplifyapp.com**

265 of Maharashtra's 358 talukas are in drought, and a mandatory 10% water cut starts on
16 October 2026. JalNyay helps officials share that shortage fairly. For cities, it
simulates the water network and finds per-ward valve settings that save the same 10% as
a blunt cut while no area runs dry (0 instead of 3 on a sample network). For villages, it
ranks drought-hit villages by need and plans tanker routes on real roads: with the same
6 tankers in Tuljapur taluka (Dharashiv), first come, first served leaves 4 of 5 high-need
villages dry and JalNyay leaves none. It also flags suspicious tanker trips by comparing GPS
traces with claimed deliveries. Everything runs serverless on AWS (Lambda, API Gateway,
DynamoDB, S3, Amazon Location Service, Amplify Hosting). The city network, wards, leaks,
livestock, tanker history and GPS data are simulated; village locations (OpenStreetMap) and
population (Census 2011) are real.

Built for WeMakeDevs x AWS "Environmental Hacks", Oct 2026 (Heat and Water track).
See [CLAUDE.md](CLAUDE.md) for project scope and rules, and the "How it works" page on the site.

## Setup (local)

```powershell
python -m venv .venv          # Python 3.12
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Part A - City engine proof of concept (SIMULATED)

```powershell
.\.venv\Scripts\python.exe -m city.partA_blunt_cut
```

Runs on the EPA Net3 sample network (`data/networks/Net3.inp`), a representative
sample network, **not** a real Maharashtra network. Writes `outputs/partA_summary.csv`
and `outputs/partA_maps.png`.

## Part B - BLUNT vs FAIR cut + leak zones (SIMULATED)

```powershell
.\.venv\Scripts\python.exe -m city.partB_run     # ~5 min on 15 workers
.\.venv\Scripts\python.exe -m pytest tests -q   # run after partB_run.py
```

`city/engine.py` holds the pure, JSON-in/JSON-out hydraulic functions (future Lambda
handlers); `evaluate_candidate()` is one FAIR plan (a future Step Functions Map job).
Wards (KMeans, 6 wards) and leaks are simulated on the sample network. Writes
`outputs/wards.json`, `outputs/partB_results.json`, `outputs/partB_compare.png`,
`outputs/partB_leaks.png`.

## Part C - Village tanker planner (Tuljapur taluka, Dharashiv)

```powershell
.\.venv\Scripts\python.exe -m village.data_prep      # builds data/village/villages.json (--refresh re-downloads)
.\.venv\Scripts\python.exe -m village.partC_run      # ~75 s (20 s OR-Tools limit + 46-solve fleet sweep on all cores)
```

Real: village locations (OpenStreetMap, ODbL) and Census 2011 village population
(via census2011.co.in, fuzzy name match). SIMULATED: livestock, source status, tanker
history, request dates, GPS traces and trip claims. The filling point (Tuljapur town)
is an assumption. `village/engine.py` holds the JSON-in/JSON-out functions: need
score, pluggable distance matrix (haversine, or a cached Amazon Location road matrix), OR-Tools routing,
naive FCFS baseline, and rule-based fraud checks. Only villages whose own source is dry (simulated)
are tanker-eligible (assumption). `plan_fleet()` sweeps 6-40 tankers; an optional second
filling point (Naldurg) can be enabled with `n_fill_points=2`.

## Part D - AWS backend (ap-south-1, AWS SAM)

### Architecture

```
client ──HTTPS──> API Gateway HTTP API ──> ApiFunction (Lambda, zip)
                  (CORS *, 5 req/s,           │  POST /jobs: validate, write job row + input, async-invoke worker
                   burst 10)                  │  GET  /jobs/{id}: status + result
                                              │  GET  /city/scenarios, /village/villages, /village/plan/latest
                                              ▼
           ┌──────────── async invoke (0 retries) ────────────┐
           ▼                        ▼                ▼               ▼
  CityEvaluateFunction    VillagePlanFunction  VillageFleetFunction  VillageFraudFunction
  (image: WNTR)           (image: OR-Tools; the three village functions share one image)
           │                        │
           └──── results ──> S3 jobs/<id>/result.json ; status ──> DynamoDB jalnyay-jobs
```

| AWS service | What it does here |
|---|---|
| API Gateway (HTTP API) | Public API; throttled to 5 req/s (burst 10); CORS `*` until Part E locks it to the Amplify URL |
| Lambda (zip) `ApiFunction` | Thin API: validation, job rows, async worker invocation, read-only endpoints |
| Lambda (container images) | `city_evaluate` (WNTR, 3008 MB, 300 s), `village_plan` (OR-Tools, 120 s), `village_fleet` (900 s), `village_fraud` (60 s). Images are needed because WNTR and OR-Tools exceed the zip size limit |
| DynamoDB (on-demand) | `jalnyay-jobs` (job status, TTL 2 days), `jalnyay-villages` (Tuljapur villages + need scores) |
| S3 (private, SSE, public access blocked, TLS only) | `networks/` (EPA Net3), `precomputed/` (Part B/C results + charts), `jobs/` (job inputs/results, deleted after 7 days) |
| Amazon Location Service (Routes V2) | Road distance/time matrix for the 34 village + filling-point locations, computed once by `scripts/build_route_matrix.py` and cached in S3 for at most 30 days (AWS Service Terms 82.4(a)(i)); haversine fallback |
| ECR | Container images for the workers (created by `sam deploy --resolve-image-repos`) |
| CloudWatch Logs | One log group per function, 7-day retention |

Why async jobs: API Gateway's integration timeout (~30 s) is shorter than the
engine runs (6-150 s, the fleet sweep ~4 min). `POST /jobs` returns a `job_id`
at once; poll `GET /jobs/{id}`.

Job types and parameters (validated in `backend/src/jalnyay_backend/validation.py`):

| type | params (defaults) |
|---|---|
| `city_evaluate` | `plan`: `FAIR` \| `BLUNT` \| `baseline` \| `custom` (+ `ward_settings` {ward: K} or `throttles` {pipe: K}) |
| `village_plan` | `n_tankers` 6, `eligible_only_dry` true, `filling_points` `["Tuljapur"]` or `["Tuljapur","Naldurg"]`, `solver_time_limit_s` 20, `distance_provider` `cache` \| `haversine`, `include_naive` true |
| `village_fleet` | `filling_points`, `eligible_only_dry`, `fleet_sizes` 6..40 step 2, `fleet_probe_sizes` 1..5, `fleet_solver_time_limit_s` 5, `distance_provider` |
| `village_fraud` | `simulate` true (demo with injected frauds; `seed`, `gps_noise_m`, `gps_gap_trip_frac`, `gps_gap_min`) or `claims` + `traces` |

Reserved concurrency: this account's Lambda concurrency limit is 10 and AWS keeps
10 unreserved, so `WorkerReservedConcurrency` defaults to 0 (off). The account
limit caps all workers together until a quota increase is granted.

### Build, test locally, deploy

```powershell
.\.venv\Scripts\python.exe -m pytest -q          # engines + backend (no AWS calls)
cd backend
sam validate --lint
sam build                                         # builds the 2 container images locally (Docker)
sam local invoke CityEvaluateFunction --event events/city_fair.json        # direct mode, no AWS calls
sam local invoke VillagePlanFunction  --event events/village_plan_2fill.json
sam deploy --guided                               # first time; shows the change set and asks before applying
cd ..
.\.venv\Scripts\python.exe scripts\stack_outputs.py         # writes backend/deploy_outputs.json (gitignored)
.\.venv\Scripts\python.exe scripts\build_route_matrix.py    # cost estimate only; add --yes --upload to build (billed)
.\.venv\Scripts\python.exe scripts\seed.py                  # precomputed results -> S3, villages -> DynamoDB
.\.venv\Scripts\python.exe scripts\smoke_test.py            # every endpoint + one job per worker
```

### Teardown

```powershell
$b = (Get-Content backend\deploy_outputs.json | ConvertFrom-Json).BucketName
aws s3 rm "s3://$b" --recursive --region ap-south-1   # the bucket must be empty before it can be deleted
cd backend; sam delete --stack-name jalnyay --region ap-south-1
```

`sam delete` also offers to delete the ECR image repositories and the SAM
artifacts bucket (`aws-sam-cli-managed-default` stack).

## Part E - Website (AWS Amplify Hosting)

Live: **https://main.dwnal659uai38.amplifyapp.com** (React + Vite + TypeScript, react-leaflet, recharts; `frontend/`).

| Screen | What is live (API / AWS Lambda) | What is precomputed |
|---|---|---|
| Home | headline numbers read from the API | countdown to 16 Oct 2026, source links |
| City | BLUNT / FAIR results from `GET /city/scenarios`; **"Try your own plan"** runs `city_evaluate` on Lambda | Net3 geometry + ward outlines bundled (`frontend/src/data/net3.json`) |
| Village | villages from DynamoDB; default plans from `GET /village/plan/latest`; **any other tanker count** runs `village_plan` on Lambda | simulated fraud demo (GPS traces + claims) bundled (`village_static.json`) |
| How it works | - | architecture diagram, AWS services, method, limitations (static page) |

- The fraud panel's "Re-run fraud check on AWS" sends the bundled claims + GPS to `village_fraud` live.
- Jobs are polled every 4 s with a per-type maximum wait; the City page sends one
  `city_evaluate {warmup: true}` on load so the first real plan avoids the cold start.
- The default village view only changes for `village_plan` jobs with `publish=true`
  (refresh it with `scripts/seed.py`); website experiments never overwrite it.
- API CORS is locked to the Amplify origin (`AllowedOrigins` in `backend/samconfig.toml`).

```powershell
.\.venv\Scripts\python.exe scripts\export_frontend_data.py   # bundled geometry + fraud demo
cd frontend; npm install; $env:VITE_API_URL="<ApiUrl>"; npm run dev   # local dev (needs CORS to allow localhost)
cd ..; .\.venv\Scripts\python.exe scripts\deploy_frontend.py  # build + manual zip deploy to Amplify
.\.venv\Scripts\python.exe scripts\smoke_test.py --origin https://main.dwnal659uai38.amplifyapp.com
.\.venv\Scripts\python.exe scripts\screenshots.py --url https://main.dwnal659uai38.amplifyapp.com/ --run-jobs
```

Screenshots of every screen (laptop + phone): `outputs/screens/`. Teardown of the
website: `aws amplify delete-app --app-id dwnal659uai38 --region ap-south-1`.

## AI tools used

- **Claude Code** (Anthropic): code generation, debugging, data research, testing,
  AWS infrastructure (SAM) and documentation. All code was written during the event.
