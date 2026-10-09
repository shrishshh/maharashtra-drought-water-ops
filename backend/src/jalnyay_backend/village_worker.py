"""Village workers: village_plan, village_fleet, village_fraud (one image, three functions).

DATA: Tuljapur taluka, Dharashiv - real village locations (OSM) + Census 2011
population; livestock, source status, requests, trips and GPS are SIMULATED.
Road distances come from a precomputed Amazon Location route matrix cached in
S3 (ROUTE_MATRIX_KEY), loaded once per container; haversine is the fallback.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from village import engine

from . import jobstore

_REPO = Path(engine.__file__).resolve().parents[1]  # repo root locally, /var/task in the image
_DATA = json.loads((_REPO / "data" / "village" / "villages.json").read_text(encoding="utf-8"))
_PART_C = json.loads((_REPO / "outputs" / "partC_results.json").read_text(encoding="utf-8"))
LABEL = ("Tuljapur taluka, Dharashiv - real village locations (OSM) + Census 2011 population; "
         "livestock / source status / requests / trips / GPS SIMULATED")
_CACHE_STATUS: str | None = None


def _ensure_distance_cache() -> str:
    """Load the S3 route matrix once per container. Never raises: on any problem
    the engine's "cache" provider falls back to haversine and says so."""
    global _CACHE_STATUS
    if _CACHE_STATUS is None:
        key = os.environ.get("ROUTE_MATRIX_KEY")
        if not key or not os.environ.get("BUCKET"):
            _CACHE_STATUS = "no route matrix configured"
        else:
            try:
                engine.set_distance_cache(jobstore.get_json(key))
                _CACHE_STATUS = f"loaded s3://{os.environ['BUCKET']}/{key}"
            except Exception as exc:  # e.g. matrix not built yet
                _CACHE_STATUS = f"not loaded ({type(exc).__name__}): using haversine"
        print("route matrix:", _CACHE_STATUS)
    return _CACHE_STATUS


def _payload_and_config(params: dict) -> tuple[dict, dict]:
    fps = [next(f for f in _DATA["fill_points"] if f["name"] == n) for n in params["filling_points"]]
    payload = {**_DATA, "fill_points": fps, "fill_point": fps[0]}
    cfg = {"eligible_only_dry": params["eligible_only_dry"], "n_fill_points": len(fps),
           "distance_provider": params["distance_provider"]}
    if params["distance_provider"] == "cache":
        _ensure_distance_cache()
    return payload, cfg


def plan(params: dict, ctx: dict) -> dict:
    t0 = time.perf_counter()
    payload, cfg = _payload_and_config(params)
    cfg.update(n_tankers=params["n_tankers"], solver_time_limit_s=params["solver_time_limit_s"])
    need = engine.need_assessment(payload, cfg)
    payload["ranked"] = need["ranked"]
    opt = engine.plan_routes(payload, cfg)
    plans = {"optimised": opt}
    if params["include_naive"]:
        plans = {"naive": engine.naive_plan(payload, cfg), **plans}
    result = {
        "label": LABEL, "params": params, "fill_points": [f["name"] for f in payload["fill_points"]],
        "distance_provider": opt["distance_provider"], "distance_cache": _CACHE_STATUS,
        "need": need, "plans": plans, "comparison": engine.compare_plans(list(plans.values()), need["ranked"]),
        "worker_runtime_s": round(time.perf_counter() - t0, 2),
    }
    if ctx.get("job_mode"):  # most recent completed plan, served by GET /village/plan/latest
        jobstore.put_json("precomputed/village_plan_latest.json",
                          {**result, "source": f"job {ctx['job_id']}", "updated_at": jobstore.now_iso()})
    return result


def fleet(params: dict, ctx: dict) -> dict:
    t0 = time.perf_counter()
    payload, cfg = _payload_and_config(params)
    cfg.update(fleet_sizes=params["fleet_sizes"], fleet_probe_sizes=params["fleet_probe_sizes"],
               fleet_solver_time_limit_s=params["fleet_solver_time_limit_s"])
    out = engine.plan_fleet(payload, cfg)  # sequential: Lambda has no shared-memory process pools
    return {"label": LABEL, "params": params, "distance_cache": _CACHE_STATUS, **out,
            "worker_runtime_s": round(time.perf_counter() - t0, 2)}


def fraud(params: dict, ctx: dict) -> dict:
    t0 = time.perf_counter()
    fps = _DATA["fill_points"]
    if params["simulate"]:
        # SIMULATED demo: GPS + claims (5 injected frauds) for the Part C optimised plan
        cfg = {k: params[k] for k in ("gps_noise_m", "gps_gap_trip_frac", "gps_gap_min")}
        plan_ = _PART_C["plans"]["optimised"]
        payload = {**_DATA, "fill_points": fps[:1], "fill_point": fps[0]}
        gps = engine.simulate_gps_and_claims(plan_, payload, cfg, seed=params["seed"])
        claims, traces = gps["claims"], gps["traces"]
    else:
        cfg, claims, traces, gps = {}, params["claims"], params["traces"], None
    report = engine.detect_fraud({"claims": claims, "traces": traces, "villages": _DATA["villages"],
                                  "fill_points": fps}, cfg)
    out = {"label": LABEL, "params": {k: v for k, v in params.items() if k not in ("claims", "traces")},
           "report": report, "worker_runtime_s": round(time.perf_counter() - t0, 2)}
    if gps is not None:
        out.update(evaluation=engine.evaluate_fraud(report, claims), claims=claims, gaps=gps["gaps"])
    return out


RUNNERS = {"village_plan": plan, "village_fleet": fleet, "village_fraud": fraud}


def plan_handler(event, context):
    return jobstore.handle(event, {"village_plan": plan})


def fleet_handler(event, context):
    return jobstore.handle(event, {"village_fleet": fleet})


def fraud_handler(event, context):
    return jobstore.handle(event, {"village_fraud": fraud})
