"""Job parameter validation shared by the API (reject early with 400) and the
workers (direct invocations). Pure Python, no heavy imports.

validate(job_type, params) -> normalised params (defaults filled in) or ValueError.
"""

from __future__ import annotations

JOB_TYPES = ("city_evaluate", "village_plan", "village_fleet", "village_fraud")
FILL_POINT_NAMES = ("Tuljapur", "Naldurg")  # Tuljapur = tanker base, must come first
CITY_PLANS = ("baseline", "BLUNT", "FAIR", "custom")
DISTANCE_PROVIDERS = ("cache", "haversine")  # "amazon_location" is billed: build the cache offline instead
MAX_K = 1e7  # throttle minor-loss coefficient cap (effectively closed)
FLEET_BUDGET_S = 700  # fleet sweep must fit the 900 s worker timeout


def _num(params: dict, key: str, default, lo, hi, integer=False):
    v = params.get(key, default)
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ValueError(f"{key} must be a number")
    if integer and int(v) != v:
        raise ValueError(f"{key} must be an integer")
    if not lo <= v <= hi:
        raise ValueError(f"{key} must be between {lo} and {hi}")
    return int(v) if integer else float(v)


def _bool(params: dict, key: str, default: bool) -> bool:
    v = params.get(key, default)
    if not isinstance(v, bool):
        raise ValueError(f"{key} must be true or false")
    return v


def _fill_points(params: dict) -> list[str]:
    fps = params.get("filling_points", ["Tuljapur"])
    if (not isinstance(fps, list) or not fps or len(set(fps)) != len(fps)
            or any(f not in FILL_POINT_NAMES for f in fps) or fps[0] != "Tuljapur"):
        raise ValueError(f"filling_points must be ['Tuljapur'] or ['Tuljapur', 'Naldurg'] (Tuljapur is the base)")
    return fps


def _provider(params: dict) -> str:
    p = params.get("distance_provider", "cache")
    if p not in DISTANCE_PROVIDERS:
        raise ValueError(f"distance_provider must be one of {DISTANCE_PROVIDERS}")
    return p


def _settings(obj, name: str) -> dict:
    if not isinstance(obj, dict) or not obj or len(obj) > 200:
        raise ValueError(f"{name} must be a non-empty object")
    out = {}
    for k, v in obj.items():
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= v <= MAX_K:
            raise ValueError(f"{name}[{k!r}] must be a number between 0 and {MAX_K:g}")
        out[str(k)] = float(v)
    return out


def validate(job_type: str, params) -> dict:
    if job_type not in JOB_TYPES:
        raise ValueError(f"type must be one of {JOB_TYPES}")
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise ValueError("params must be an object")

    if job_type == "city_evaluate":
        if params.get("warmup") is True:  # website page load: start a container, load WNTR, run the baseline
            return {"warmup": True}
        plan = params.get("plan", "FAIR")
        if plan not in CITY_PLANS:
            raise ValueError(f"plan must be one of {CITY_PLANS}")
        out = {"plan": plan}
        if plan == "custom":
            if "ward_settings" in params:
                out["ward_settings"] = _settings(params["ward_settings"], "ward_settings")
            elif "throttles" in params:
                out["throttles"] = _settings(params["throttles"], "throttles")
            else:
                raise ValueError("custom plan needs ward_settings {ward: K} or throttles {pipe: K}")
        return out

    if job_type == "village_plan":
        return {
            "n_tankers": _num(params, "n_tankers", 6, 1, 60, integer=True),
            "eligible_only_dry": _bool(params, "eligible_only_dry", True),
            "filling_points": _fill_points(params),
            "solver_time_limit_s": _num(params, "solver_time_limit_s", 20, 1, 60, integer=True),
            "distance_provider": _provider(params),
            "include_naive": _bool(params, "include_naive", True),
            # publish=true jobs refresh the website's default view (GET /village/plan/latest);
            # experiments and smoke tests leave it alone
            "publish": _bool(params, "publish", False),
        }

    if job_type == "village_fleet":
        sizes = params.get("fleet_sizes", list(range(6, 41, 2)))
        probes = params.get("fleet_probe_sizes", [1, 2, 3, 4, 5])
        for name, lst in (("fleet_sizes", sizes), ("fleet_probe_sizes", probes)):
            if (not isinstance(lst, list) or len(lst) > 30
                    or any(isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= 60 for n in lst)):
                raise ValueError(f"{name} must be a list of up to 30 integers between 1 and 60")
        if not sizes:
            raise ValueError("fleet_sizes must not be empty")
        limit = _num(params, "fleet_solver_time_limit_s", 5, 1, 10, integer=True)
        n_solves = 2 * len(set(sizes) | set(probes))
        if n_solves * limit > FLEET_BUDGET_S:
            raise ValueError(f"{n_solves} solves x {limit} s exceeds the {FLEET_BUDGET_S} s budget; "
                             "use fewer sizes or a shorter fleet_solver_time_limit_s")
        return {"fleet_sizes": sizes, "fleet_probe_sizes": probes, "fleet_solver_time_limit_s": limit,
                "eligible_only_dry": _bool(params, "eligible_only_dry", True),
                "filling_points": _fill_points(params), "distance_provider": _provider(params)}

    # village_fraud
    if params.get("simulate", False) is True:
        gap = params.get("gps_gap_min", [2, 5])
        if (not isinstance(gap, list) or len(gap) != 2
                or any(isinstance(g, bool) or not isinstance(g, int) or not 0 <= g <= 60 for g in gap) or gap[0] > gap[1]):
            raise ValueError("gps_gap_min must be [min, max] minutes, 0-60")
        return {"simulate": True, "seed": _num(params, "seed", 99, 0, 10**6, integer=True),
                "gps_noise_m": _num(params, "gps_noise_m", 30, 0, 200),
                "gps_gap_trip_frac": _num(params, "gps_gap_trip_frac", 0.10, 0, 1), "gps_gap_min": gap}
    claims, traces = params.get("claims"), params.get("traces")
    if not isinstance(claims, list) or not claims or len(claims) > 5000:
        raise ValueError("village_fraud needs simulate=true, or claims (list, max 5000) + traces")
    if not isinstance(traces, dict):
        raise ValueError("traces must be an object {tanker: [[minute, lat, lon], ...]}")
    for c in claims:
        if not isinstance(c, dict) or not {"claim_id", "tanker", "village_id", "litres", "claimed_min"} <= set(c):
            raise ValueError("each claim needs claim_id, tanker, village_id, litres, claimed_min")
    return {"simulate": False, "claims": claims, "traces": traces}
