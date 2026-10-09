"""city_evaluate worker: evaluate one throttle plan on the EPA Net3 sample network.

NETWORK: representative sample network (EPA Net3), not a real Maharashtra
network; wards are SIMULATED (KMeans). Plans:
  baseline | BLUNT | FAIR  -> settings from the Part B results bundled in the image
  custom                   -> {ward_settings: {ward: K}} applied to each ward's
                              inlet pipes (stronger throttle wins on shared
                              pipes, as in engine.evaluate_candidate), or
                              {throttles: {pipe: K}}
"""

from __future__ import annotations

import json
import time

from city import engine

from . import jobstore

_WARDS = json.loads((engine.REPO / "outputs" / "wards.json").read_text())
_PART_B = json.loads((engine.REPO / "outputs" / "partB_results.json").read_text())
_BASELINE: dict | None = None  # one baseline run per container (warm invocations reuse it)


def _baseline() -> dict:
    global _BASELINE
    if _BASELINE is None:
        _BASELINE = engine.run_scenario({})
    return _BASELINE


def _throttles(params: dict) -> tuple[dict, dict | None]:
    plan = params["plan"]
    if plan == "baseline":
        return {}, None
    if plan in ("BLUNT", "FAIR"):
        sc = _PART_B["scenarios"][plan]
        return sc["throttle_settings"], sc.get("ward_settings")
    if "throttles" in params:
        unknown = set(params["throttles"]) - set(engine.build_network().pipe_name_list)
        if unknown:
            raise ValueError(f"unknown pipes: {sorted(unknown)[:5]}")
        return params["throttles"], None
    wards = _WARDS["wards"]
    unknown = set(params["ward_settings"]) - set(wards)
    if unknown:
        raise ValueError(f"unknown wards: {sorted(unknown)}; valid: {sorted(wards)}")
    throttles: dict = {}
    for w, k in params["ward_settings"].items():
        for p in wards[w]["inlet_pipes"]:
            throttles[p] = max(throttles.get(p, 0.0), k)
    return throttles, params["ward_settings"]


def evaluate(params: dict, ctx: dict) -> dict:
    t0 = time.perf_counter()
    if params.get("warmup"):
        _baseline()  # also caches the baseline, so the next real plan needs one simulation
        return {"warmup": True, "note": "container warm: WNTR loaded, baseline simulated",
                "worker_runtime_s": round(time.perf_counter() - t0, 2)}
    throttles, ward_settings = _throttles(params)
    base = _baseline()
    run = engine.run_scenario({"throttles": throttles}) if throttles else base
    metrics = engine.score(run, base["vol_delivered_m3"], _WARDS)
    return {
        "label": engine.NETWORK_LABEL + " - wards SIMULATED",
        "plan": params["plan"],
        "throttle_settings": throttles,
        "ward_settings": ward_settings,
        "metrics": metrics,
        "in_target_band": engine.in_band(metrics),
        "junction_ratio": run["junction_ratio"],
        "water_balance": run["water_balance"],
        "baseline_vol_m3": round(base["vol_delivered_m3"], 1),
        "sim_runtime_s": run["runtime_s"],
        "worker_runtime_s": round(time.perf_counter() - t0, 2),
    }


def handler(event, context):
    return jobstore.handle(event, {"city_evaluate": evaluate})
