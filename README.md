# maharashtra-drought-water-ops

Water operations toolkit for Maharashtra's drought response (WeMakeDevs x AWS "Environmental Hacks", Oct 2026, Heat and Water track).
See [CLAUDE.md](CLAUDE.md) for project scope and rules.

## Setup (local)

```powershell
python -m venv .venv          # Python 3.12
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Part A - City engine proof of concept (SIMULATED)

```powershell
.\.venv\Scripts\python.exe city\partA_blunt_cut.py
```

Runs on the EPA Net3 sample network (`data/networks/Net3.inp`), a representative
sample network, **not** a real Maharashtra network. Writes `outputs/partA_summary.csv`
and `outputs/partA_maps.png`.

## Part B - BLUNT vs FAIR cut + leak zones (SIMULATED)

```powershell
.\.venv\Scripts\python.exe city\partB_run.py     # ~5 min on 15 workers
.\.venv\Scripts\python.exe -m pytest tests -q   # run after partB_run.py
```

`city/engine.py` holds the pure, JSON-in/JSON-out hydraulic functions (future Lambda
handlers); `evaluate_candidate()` is one FAIR plan (a future Step Functions Map job).
Wards (KMeans, 6 wards) and leaks are simulated on the sample network. Writes
`outputs/wards.json`, `outputs/partB_results.json`, `outputs/partB_compare.png`,
`outputs/partB_leaks.png`.

## Part C - Village tanker planner (Tuljapur taluka, Dharashiv)

```powershell
.\.venv\Scripts\python.exe village\data_prep.py      # builds data/village/villages.json (--refresh re-downloads)
.\.venv\Scripts\python.exe village\partC_run.py      # ~40 s (30 s OR-Tools time limit)
```

Real: village locations (OpenStreetMap, ODbL) and Census 2011 village population
(via census2011.co.in, fuzzy name match). SIMULATED: livestock, source status, tanker
history, request dates, GPS traces and trip claims. The filling point (Tuljapur town)
is an assumption. `village/engine.py` holds the JSON-in/JSON-out functions: need
score, pluggable distance matrix (Amazon Location stub for Part D), OR-Tools routing,
naive FCFS baseline, and rule-based fraud checks. Only villages whose own source is dry (simulated)
are tanker-eligible (assumption). `plan_fleet()` sweeps 6-40 tankers; an optional second
filling point (Naldurg) can be enabled with `n_fill_points=2`.

## AI tools used

- Claude Code (Anthropic) - code generation, debugging, and documentation.
