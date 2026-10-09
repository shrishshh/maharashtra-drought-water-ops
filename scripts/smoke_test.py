"""End-to-end smoke test of the deployed API (after deploy + stack_outputs.py + seed.py).

Hits every endpoint, runs one job per worker (twice, to measure cold vs warm),
polls to completion, and compares the numbers with the local Part B / Part C
results in outputs/ (within rounding; OR-Tools plans are time-limited
heuristics, so the optimised plan is compared with a tolerance).

Usage:  .venv\\Scripts\\python.exe scripts\\smoke_test.py [--api URL] [--with-fleet] [--no-repeat]
Only standard library HTTP calls to the public API.
"""

import argparse
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PART_B = json.loads((REPO / "outputs" / "partB_results.json").read_text())
PART_C = json.loads((REPO / "outputs" / "partC_results.json").read_text(encoding="utf-8"))
RESULTS: list = []


def http(method, url, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"content-type": "application/json"})
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            status, text = r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        status, text = e.code, e.read().decode()
    return status, (json.loads(text) if text else None), time.perf_counter() - t0


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))


def run_job(api, job_type, params, timeout_s):
    t0 = time.perf_counter()
    status, body, _ = http("POST", f"{api}/jobs", {"type": job_type, "params": params})
    if status != 202:
        raise RuntimeError(f"POST /jobs {job_type} -> {status} {body}")
    job_id = body["job_id"]
    while True:
        time.sleep(2)
        status, job, _ = http("GET", f"{api}/jobs/{job_id}")
        if job["status"] in ("done", "failed"):
            break
        if time.perf_counter() - t0 > timeout_s:
            raise RuntimeError(f"{job_type} job {job_id} still {job['status']} after {timeout_s} s")
    job["wall_s"] = round(time.perf_counter() - t0, 1)
    if job["status"] == "failed":
        raise RuntimeError(f"{job_type} job {job_id} failed: {job.get('error')}")
    return job


def timed(api, job_type, params, timeout_s, repeat, timings):
    jobs = [run_job(api, job_type, params, timeout_s) for _ in range(2 if repeat else 1)]
    for i, j in enumerate(jobs):
        timings.append((job_type, "cold" if j.get("cold_start") else "warm", j.get("runtime_s"), j["wall_s"]))
    return jobs[-1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", help="API base URL (default: backend/deploy_outputs.json)")
    ap.add_argument("--with-fleet", action="store_true", help="also run the full fleet sweep (~4-5 min)")
    ap.add_argument("--no-repeat", action="store_true", help="run each job once (skip warm-start timing)")
    args = ap.parse_args()
    api = (args.api or json.loads((REPO / "backend" / "deploy_outputs.json").read_text())["ApiUrl"]).rstrip("/")
    repeat, timings = not args.no_repeat, []
    print(f"API: {api}\n")

    print("Read-only endpoints")
    status, city, dt = http("GET", f"{api}/city/scenarios")
    check("GET /city/scenarios", status == 200 and set(city["scenarios"]) == {"BLUNT", "FAIR"}, f"{dt:.2f} s")
    for sc in ("BLUNT", "FAIR"):
        check(f"  {sc} metrics = local Part B", all(city["scenarios"][sc][k] == PART_B["scenarios"][sc][k]
              for k in ("reduction_pct", "underserved_count", "dry_count", "min_ratio")))
    check("  chart links present", "compare" in city.get("images", {}))
    status, vil, dt = http("GET", f"{api}/village/villages")
    check("GET /village/villages", status == 200 and vil["count"] == 32 and vil["villages"][0]["rank"] == 1,
          f"{vil.get('count')} villages, {dt:.2f} s")
    status, latest, dt = http("GET", f"{api}/village/plan/latest")
    check("GET /village/plan/latest", status == 200 and len(latest["comparison"]) >= 2, f"{dt:.2f} s")

    print("\nValidation")
    check("POST /jobs bad type -> 400", http("POST", f"{api}/jobs", {"type": "nope"})[0] == 400)
    check("POST /jobs bad filling_points -> 400",
          http("POST", f"{api}/jobs", {"type": "village_plan", "params": {"filling_points": ["Naldurg"]}})[0] == 400)
    check("GET /jobs/<unknown> -> 404", http("GET", f"{api}/jobs/{'0' * 32}")[0] == 404)

    print("\ncity_evaluate (FAIR) vs local Part B")
    job = timed(api, "city_evaluate", {"plan": "FAIR"}, 330, repeat, timings)
    m, local = job["result"]["metrics"], PART_B["scenarios"]["FAIR"]
    check("FAIR under-served / dry identical", (m["underserved_count"], m["dry_count"]) ==
          (local["underserved_count"], local["dry_count"]), f"{m['underserved_count']} / {m['dry_count']}")
    check("FAIR reduction % within 0.05", abs(m["reduction_pct"] - local["reduction_pct"]) <= 0.05,
          f"{m['reduction_pct']} vs {local['reduction_pct']}")
    check("FAIR min ratio within 0.002", abs(m["min_ratio"] - local["min_ratio"]) <= 0.002)
    check("water balance ok", job["result"]["water_balance"]["ok"])
    custom = run_job(api, "city_evaluate", {"plan": "custom", "ward_settings": {"Ward A": 400, "Ward B": 1150}}, 330)
    check("custom ward settings job done", custom["status"] == "done",
          f"reduction {custom['result']['metrics']['reduction_pct']}%")

    print("\nvillage_plan (Tuljapur, haversine) vs local Part C")
    job = timed(api, "village_plan", {"n_tankers": 6, "filling_points": ["Tuljapur"],
                                      "distance_provider": "haversine"}, 150, repeat, timings)
    naive, opt = job["result"]["comparison"]
    check("naive plan identical (deterministic)", naive == PART_C["comparison"][0])
    lo = PART_C["comparison"][1]
    check("optimised: same high-need villages unserved", opt["high_need_unserved"] == lo["high_need_unserved"],
          f"{opt['high_need_unserved']}")
    check("optimised: litres within 10% (time-limited heuristic)",
          abs(opt["litres_delivered"] - lo["litres_delivered"]) <= 0.10 * lo["litres_delivered"],
          f"{opt['litres_delivered']:,} vs {lo['litres_delivered']:,}")

    print("\nvillage_plan (Tuljapur + Naldurg, road distances from the cached matrix)")
    job = run_job(api, "village_plan", {"n_tankers": 6, "filling_points": ["Tuljapur", "Naldurg"]}, 150)
    row = job["result"]["comparison"][-1]
    check("2 filling points job done", job["status"] == "done",
          f"{row['litres_delivered']:,} L, {row['villages_served']} villages; {job['result']['distance_provider']}")

    print("\nvillage_fraud (simulated demo) vs local")
    job = timed(api, "village_fraud", {"simulate": True}, 90, repeat, timings)
    ev = job["result"]["evaluation"]
    check("precision / recall = local", (ev["precision"], ev["recall"]) ==
          (PART_C["fraud"]["evaluation"]["precision"], PART_C["fraud"]["evaluation"]["recall"]),
          f"{ev['precision']} / {ev['recall']}")

    if args.with_fleet:
        print("\nvillage_fleet (full sweep)")
        job = run_job(api, "village_fleet", {"distance_provider": "haversine"}, 960)
        timings.append(("village_fleet", "cold" if job.get("cold_start") else "warm", job.get("runtime_s"), job["wall_s"]))
        f, lf = job["result"], PART_C["fleet"]
        check("fleet threshold (a) within 1 of local",
              abs(f["min_tankers_every_high_need_one_load"] - lf["min_tankers_every_high_need_one_load"]) <= 1,
              f"{f['min_tankers_every_high_need_one_load']} vs {lf['min_tankers_every_high_need_one_load']}")
        b, lb = f["min_tankers_full_human_need"], lf["min_tankers_full_human_need"]
        check("fleet threshold (b) within 2 of local", b is not None and lb is not None and abs(b - lb) <= 2,
              f"{b} vs {lb}")
    else:
        job = run_job(api, "village_fleet", {"fleet_sizes": [6, 10], "fleet_probe_sizes": [],
                                             "fleet_solver_time_limit_s": 3, "distance_provider": "haversine"}, 300)
        timings.append(("village_fleet (2 sizes)", "cold" if job.get("cold_start") else "warm",
                        job.get("runtime_s"), job["wall_s"]))
        by_n = {r["n_tankers"]: r for r in job["result"]["sweep"]}
        local = {r["n_tankers"]: r for r in PART_C["fleet"]["sweep"]}
        check("fleet (6, 10 tankers) coverage within 2 points of local",
              all(abs(by_n[n]["need_covered_pct"] - local[n]["need_covered_pct"]) <= 2 for n in (6, 10)),
              ", ".join(f"{n}: {by_n[n]['need_covered_pct']}%" for n in (6, 10)))

    print("\nWorker timings (runtime_s = inside the worker; wall_s = submit -> done, incl. polling every 2 s)")
    for t in timings:
        print(f"  {t[0]:<26} {t[1]:<5} runtime {t[2]} s   wall {t[3]} s")
    failed = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
