"""City engine: hydraulic scenarios for a fair 10% water cut.

NETWORK: representative sample network (EPA Net3), not a real Maharashtra network.
All wards, leaks and results produced here are SIMULATED.

Public functions take plain dicts / JSON-compatible values and return
JSON-serialisable dicts, so each can become a Lambda handler later:

  run_scenario(spec, config)        one hydraulic simulation -> volumes, ratios, balance
  assign_wards(config, baseline)    KMeans wards + boundary links + inlet pipes
  score(run, baseline_vol, wards)   fairness metrics for one run
  solve_throttle(payload)           scale a throttle plan until the cut hits the target band
  evaluate_candidate(payload)       one FAIR candidate plan (a future Step Functions job)
  blunt_scenario / fair_search      scenario drivers
  leak_detection(...)               night-inflow anomaly per ward

Modelling notes (from Part A):
  - WNTRSimulator, not EpanetSimulator: under EPANET, empty tanks kept
    "supplying" water with a frozen level. water_balance_check() catches this.
  - Run SIM_DAYS and score only the last EVAL_HOURS, so tank storage cannot
    mask a sustained cut.
  - Pump speed is applied via affinity laws on pump curves (WNTRSimulator only
    supports speed 1.0).
"""

from __future__ import annotations

import math
import time
import warnings
from pathlib import Path

import numpy as np
import wntr

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", message="Pump .* exceeded its maximum flow")

REPO = Path(__file__).resolve().parents[1]
NETWORK_LABEL = "Representative sample network (EPA Net3), SIMULATED"

DEFAULT_CONFIG: dict = {
    "inp_file": "data/networks/Net3.inp",  # EPA Net3 sample, NOT a real Maharashtra network
    "sim_days": 7,
    "eval_hours": 24,
    "timestep_s": 3600,
    # 12 m = CPHEEO (1999) residual pressure for two-storey buildings (verify vs latest CPHEEO/MJP).
    "required_pressure_m": 12.0,
    "minimum_pressure_m": 0.0,
    "pressure_exponent": 0.5,
    "target_reduction": 0.10,
    "reduction_band": [0.095, 0.105],
    "dry_threshold": 0.5,
    "underserved_threshold": 0.8,
    "source_mains": ["60", "101"],  # Net3 River main + Lake main: all supply passes through these
    "max_minor_loss": 1e7,  # cap on throttle loss coefficient K (effectively closed)
    # Water balance tolerances (see water_balance_check).
    "continuity_tol_m3s": 1e-6,
    "tank_storage_tol_m3": 1.0,
    "tank_storage_tol_frac": 0.01,
    "phantom_min_steps": 3,
    "n_wards": 6,
    "ward_seed": 42,
}


class WaterBalanceError(RuntimeError):
    """Raised when a simulation does not conserve water."""


class SimulationIncomplete(RuntimeError):
    """Raised when WNTRSimulator stops before the end (it returns partial results silently)."""


def _cfg(config: dict | None) -> dict:
    return {**DEFAULT_CONFIG, **(config or {})}


# --------------------------------------------------------------------------
# Network + simulation
# --------------------------------------------------------------------------
def build_network(spec: dict | None = None, config: dict | None = None) -> wntr.network.WaterNetworkModel:
    """spec keys (all optional):
    speed_factor: float            all pumps at this fraction of rated speed
    throttles: {pipe: K}           minor-loss coefficient added to these pipes
    leaks: [{junction, area_m2, discharge_coeff}]
    """
    spec, cfg = spec or {}, _cfg(config)
    wn = wntr.network.WaterNetworkModel(str(REPO / cfg["inp_file"]))
    duration_s = cfg["sim_days"] * 24 * 3600
    wn.options.time.duration = duration_s
    for attr in ("hydraulic_timestep", "report_timestep", "pattern_timestep"):
        setattr(wn.options.time, attr, cfg["timestep_s"])
    wn.options.quality.parameter = "NONE"
    wn.options.hydraulic.demand_model = "PDD"
    wn.options.hydraulic.required_pressure = cfg["required_pressure_m"]
    wn.options.hydraulic.minimum_pressure = cfg["minimum_pressure_m"]
    wn.options.hydraulic.pressure_exponent = cfg["pressure_exponent"]

    s = spec.get("speed_factor", 1.0)
    if s != 1.0:
        for _, pump in wn.pumps():
            curve = wn.get_curve(pump.pump_curve_name)
            curve.points = [(q * s, h * s**2) for q, h in curve.points]

    for pipe, k in (spec.get("throttles") or {}).items():
        wn.get_link(pipe).minor_loss = min(float(k), cfg["max_minor_loss"])

    for leak in spec.get("leaks") or []:
        wn.get_node(leak["junction"]).add_leak(
            wn, area=leak["area_m2"], discharge_coeff=leak.get("discharge_coeff", 0.75), start_time=0)
    return wn


def simulate(spec: dict | None = None, config: dict | None = None):
    """Run WNTRSimulator and enforce the water balance. Returns (wn, results, balance)."""
    wn = build_network(spec, config)
    results = wntr.sim.WNTRSimulator(wn).run_sim()
    end = results.node["demand"].index[-1]
    if end < wn.options.time.duration:
        raise SimulationIncomplete(f"simulation stopped at t={end / 3600:.0f} h of "
                                   f"{wn.options.time.duration / 3600:.0f} h (did not converge)")
    return wn, results, water_balance_check(wn, results, config)


def water_balance_check(wn, results, config: dict | None = None) -> dict:
    """Fail loudly if water in != water out.

    1. Node continuity at every timestep: reservoir supply + tank outflow
       == junction delivery + leak outflow (tolerance continuity_tol_m3s).
    2. Phantom water: a tank pinned at its min level must not keep supplying
       water (and at max level must not keep taking it). This is the Part A
       EPANET failure: empty tanks "supplying" ~690 L/s for days while their
       level stayed frozen. Fails if this persists for phantom_min_steps
       consecutive steps; a single step is allowed because WNTR reports the
       flow at the start of an hour and closes the tank pipe seconds later
       when the tank fills/empties. Tolerance per step:
       tank_storage_tol_m3 + tank_storage_tol_frac x |q| dt.
    Reported, not enforced: each tank's whole-run storage mismatch (level
    change vs rectangle-rule integral of reported inflow) as a fraction of
    throughput. Results are only reported hourly but WNTR takes unreported
    intermediate steps (pumps switching on tank levels, a tank emptying and
    closing seconds into an hour), so this is ~2-26% on Net3 and shrinks at
    finer timesteps; a strict per-step volume match is not possible.
    """
    cfg = _cfg(config)
    J, R, T = wn.junction_name_list, wn.reservoir_name_list, wn.tank_name_list
    dem = results.node["demand"]
    leak = results.node["leak_demand"][J].sum(axis=1) if "leak_demand" in results.node else 0.0
    supply = -dem[R].sum(axis=1) - dem[T].sum(axis=1)  # into the pipe network
    use = dem[J].sum(axis=1) + leak
    resid = float((supply - use).abs().max())
    if not resid <= cfg["continuity_tol_m3s"]:
        raise WaterBalanceError(f"Node continuity violated: max |in - out| = {resid:.3e} m3/s")

    dt = cfg["timestep_s"]
    level_eps = 1e-3  # m
    storage_err = {}
    for t in T:
        tank = wn.get_node(t)
        area = math.pi * (tank.diameter / 2) ** 2
        lvl = results.node["pressure"][t].to_numpy()
        q = dem[t].to_numpy()  # + = filling
        v0 = q[:-1] * dt
        tol = cfg["tank_storage_tol_m3"] + cfg["tank_storage_tol_frac"] * np.abs(v0)
        empty = (lvl[:-1] <= tank.min_level + level_eps) & (lvl[1:] <= tank.min_level + level_eps)
        full = (lvl[:-1] >= tank.max_level - level_eps) & (lvl[1:] >= tank.max_level - level_eps)
        phantom = np.where(empty, -v0, 0.0) + np.where(full, v0, 0.0)  # supply from empty / fill of full
        bad = phantom > tol
        run_len = np.zeros(len(bad), dtype=int)  # consecutive phantom steps ending at i
        for i, b in enumerate(bad):
            run_len[i] = (run_len[i - 1] + 1 if i else 1) if b else 0
        if (run_len >= cfg["phantom_min_steps"]).any():
            i = int(np.argmax(run_len >= cfg["phantom_min_steps"]))
            raise WaterBalanceError(
                f"Tank {t} at its {'min' if empty[i] else 'max'} level reports {q[i] * 1000:,.1f} L/s "
                f"with no level change for {cfg['phantom_min_steps']}+ steps (from step "
                f"{i - cfg['phantom_min_steps'] + 1}) - phantom water")
        throughput = float(np.abs(v0).sum())
        mismatch = abs((lvl[-1] - lvl[0]) * area - float(v0.sum()))
        storage_err[t] = round(float(mismatch / throughput), 4) if throughput else 0.0
    return {"ok": True, "max_continuity_residual_m3s": resid, "tank_storage_mismatch_frac": storage_err}


def _eval_steps(results, cfg) -> list:
    total_s = cfg["sim_days"] * 24 * 3600
    t0 = total_s - cfg["eval_hours"] * 3600
    return [t for t in results.node["demand"].index if t0 <= t < total_s]


def demand_junctions(wn) -> list[str]:
    return [j for j in wn.junction_name_list
            if sum(d.base_value for d in wn.get_node(j).demand_timeseries_list) > 0]


def run_scenario(spec: dict | None = None, config: dict | None = None) -> dict:
    """One scenario -> JSON-serialisable volumes, per-junction ratios, balance.

    Extra spec keys:
      ward_boundaries: {ward: [{link, sign}]}  -> report ward_night_inflow_m3
      night_window_h: [start, end]             (default [2, 4], clock hours, last day)
      return_link_flows: bool                  -> mean flow per link in eval window
    """
    spec, cfg = spec or {}, _cfg(config)
    t_start = time.perf_counter()
    wn, res, balance = simulate(spec, cfg)
    runtime = time.perf_counter() - t_start

    steps = _eval_steps(res, cfg)
    dj = demand_junctions(wn)
    dt = cfg["timestep_s"]
    delivered = res.node["demand"].loc[steps, dj].clip(lower=0).sum() * dt
    expected = wntr.metrics.expected_demand(wn).loc[steps, dj].sum() * dt
    ratio = delivered / expected

    out = {
        "vol_delivered_m3": float(delivered.sum()),
        "vol_expected_m3": float(expected.sum()),
        "junction_ratio": {j: round(float(ratio[j]), 4) for j in dj},
        "junction_expected_m3": {j: round(float(expected[j]), 3) for j in dj},
        "leak_volume_m3": float(res.node["leak_demand"].loc[steps].sum().sum() * dt) if "leak_demand" in res.node else 0.0,
        "water_balance": balance,
        "runtime_s": round(runtime, 2),
    }

    flows = res.link["flowrate"]
    if spec.get("return_link_flows"):
        out["link_mean_flow_m3s"] = {l: float(v) for l, v in flows.loc[steps].mean().items()}

    if spec.get("ward_boundaries"):
        h0, h1 = spec.get("night_window_h", [2, 4])
        day0 = steps[0]
        night = [t for t in steps if day0 + h0 * 3600 <= t < day0 + h1 * 3600]
        out["ward_night_inflow_m3"] = {
            ward: float(sum(b["sign"] * flows.loc[night, b["link"]].sum() for b in links) * dt)
            for ward, links in spec["ward_boundaries"].items()
        }
    return out


# --------------------------------------------------------------------------
# Wards (SIMULATED, on the sample network)
# --------------------------------------------------------------------------
def assign_wards(config: dict | None = None, baseline_link_flow: dict | None = None) -> dict:
    """Split demand junctions into n_wards wards with KMeans on coordinates.

    - Ward letters A.. are assigned north to south (by centroid y) for stable naming.
    - Zero-demand junctions join the nearest ward centroid (needed to find boundaries).
    - Source nodes = reservoirs, tanks, and pump-station nodes; they belong to no ward.
    - boundary_links: every link (pipe or pump) crossing the ward boundary, with
      sign = +1 if positive flow enters the ward. Used for bulk metering.
    - inlet_pipes: boundary *pipes* (throttleable) that feed the ward: all pipes
      from a source node, plus ward-to-ward pipes whose baseline mean flow
      (eval window) enters this ward.
    """
    from sklearn.cluster import KMeans

    cfg = _cfg(config)
    wn = build_network(config=cfg)
    dj = demand_junctions(wn)
    xy = np.array([wn.get_node(j).coordinates for j in dj])
    km = KMeans(n_clusters=cfg["n_wards"], random_state=cfg["ward_seed"], n_init=10).fit(xy)

    order = np.argsort(-km.cluster_centers_[:, 1])  # north first
    names = {int(c): f"Ward {chr(65 + i)}" for i, c in enumerate(order)}
    centers = {names[c]: km.cluster_centers_[c] for c in names}

    sources = set(wn.reservoir_name_list) | set(wn.tank_name_list)
    for _, pump in wn.pumps():
        sources |= {pump.start_node_name, pump.end_node_name}

    node_ward = {j: names[int(c)] for j, c in zip(dj, km.labels_)}
    for j in wn.junction_name_list:
        if j not in node_ward and j not in sources:
            p = np.array(wn.get_node(j).coordinates)
            node_ward[j] = min(centers, key=lambda w: np.linalg.norm(centers[w] - p))

    wards = {w: {"demand_junctions": [], "other_junctions": [], "boundary_links": [], "inlet_pipes": [],
                 "centroid": [round(float(v), 2) for v in centers[w]]} for w in sorted(centers)}
    for j, w in node_ward.items():
        wards[w]["demand_junctions" if j in dj else "other_junctions"].append(j)

    for name, link in wn.links():
        a, b = link.start_node_name, link.end_node_name
        wa, wb = node_ward.get(a), node_ward.get(b)
        if wa == wb:
            continue  # internal to a ward, or source-to-source
        is_pipe = isinstance(link, wntr.network.Pipe)
        for w, sign, other in ((wb, +1, a), (wa, -1, b)):
            if w is None:
                continue
            wards[w]["boundary_links"].append({"link": name, "sign": sign})
            if not is_pipe:
                continue
            from_source = other in sources
            enters = baseline_link_flow is not None and sign * baseline_link_flow.get(name, 0.0) > 0
            if from_source or enters:
                wards[w]["inlet_pipes"].append(name)

    return {
        "label": "SIMULATED wards on a representative sample network (EPA Net3) - not real wards",
        "method": f"KMeans(n_clusters={cfg['n_wards']}, random_state={cfg['ward_seed']}) on demand-junction coordinates",
        "source_nodes": sorted(sources),
        "junction_to_ward": dict(sorted(node_ward.items())),
        "wards": wards,
    }


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------
def score(run: dict, baseline_vol_m3: float, wards: dict | None = None, config: dict | None = None) -> dict:
    cfg = _cfg(config)
    ratios = run["junction_ratio"]
    vals = np.array(list(ratios.values()))
    out = {
        "reduction_pct": round(100 * (1 - run["vol_delivered_m3"] / baseline_vol_m3), 3),
        "underserved_count": int((vals < cfg["underserved_threshold"]).sum()),
        "dry_count": int((vals < cfg["dry_threshold"]).sum()),
        "min_ratio": round(float(vals.min()), 4),
        "weighted_mean_ratio": round(run["vol_delivered_m3"] / run["vol_expected_m3"], 4),
    }
    if wards:
        out["ward_mean_ratio"] = {
            w: round(float(np.mean([ratios[j] for j in info["demand_junctions"]])), 4)
            for w, info in wards["wards"].items()
        }
    return out


def fairness_key(metrics: dict) -> tuple:
    """Lower is better: fewest under-served, then highest min ratio, then highest weighted mean."""
    return (metrics["underserved_count"], -metrics["min_ratio"], -metrics["weighted_mean_ratio"])


def in_band(metrics: dict, config: dict | None = None) -> bool:
    lo, hi = _cfg(config)["reduction_band"]
    return 100 * lo <= metrics["reduction_pct"] <= 100 * hi


# --------------------------------------------------------------------------
# Throttle plans
# --------------------------------------------------------------------------
def _solve_throttle(payload: dict, history: list) -> dict:
    """Scale a throttle plan K_pipe = s * weight_pipe until the cut is in band.

    payload: {weights: {pipe: w}, baseline_vol_m3, config?, wards?, max_sims?}
    Search is on log(s) with the Illinois variant of regula falsi; the cut
    increases monotonically with s.
    """
    cfg = _cfg(payload.get("config"))
    weights, base_vol = payload["weights"], payload["baseline_vol_m3"]
    lo_b, hi_b = cfg["reduction_band"]
    target = cfg["target_reduction"]
    max_sims = payload.get("max_sims", 14)

    def f(log_s: float):
        s = 10 ** log_s
        try:
            run = run_scenario({"throttles": {p: s * w for p, w in weights.items()}}, cfg)
        except SimulationIncomplete as exc:
            history.append({"scale": s, "error": str(exc)})
            raise
        m = score(run, base_vol, payload.get("wards"), cfg)
        history.append({"scale": s, "reduction_pct": m["reduction_pct"], "runtime_s": run["runtime_s"]})
        return m["reduction_pct"] / 100 - target, s, run, m

    def done(r):
        return lo_b <= r[0] + target <= hi_b

    # Bracket: g(a) < 0 < g(b)
    a, b = 1.0, 4.0
    ra, rb = f(a), f(b)
    for r in (ra, rb):
        if done(r):
            return _solved(r, weights, history)
    while ra[0] > 0 and len(history) < max_sims:
        b, rb, a = a, ra, a - 1.5
        ra = f(a)
        if done(ra):
            return _solved(ra, weights, history)
    while rb[0] < 0 and len(history) < max_sims and 10 ** b * max(weights.values()) < cfg["max_minor_loss"]:
        a, ra, b = b, rb, b + 1.0
        rb = f(b)
        if done(rb):
            return _solved(rb, weights, history)
    if not (ra[0] < 0 < rb[0]):
        return {"feasible": False, "reason": "could not bracket the target band", "history": history}

    side = 0
    while len(history) < max_sims:
        c = b - rb[0] * (b - a) / (rb[0] - ra[0])
        rc = f(c)
        if done(rc):
            return _solved(rc, weights, history)
        if rc[0] < 0:
            a, ra = c, rc
            if side == -1:
                rb = (rb[0] / 2, *rb[1:])
            side = -1
        else:
            b, rb = c, rc
            if side == +1:
                ra = (ra[0] / 2, *ra[1:])
            side = +1
    return {"feasible": False, "reason": "max simulations reached", "history": history}


def solve_throttle(payload: dict) -> dict:
    """Scale a throttle plan K_pipe = s * weight_pipe until the cut is in band.

    payload: {weights: {pipe: w}, baseline_vol_m3, config?, wards?, max_sims?}
    Returns feasible=False (with the reason) if the band cannot be bracketed
    or a simulation fails to converge.
    """
    history: list = []
    try:
        return _solve_throttle(payload, history)
    except SimulationIncomplete as exc:
        return {"feasible": False, "reason": str(exc), "history": history}


def _solved(r, weights, history) -> dict:
    _, s, run, metrics = r
    return {
        "feasible": True,
        "scale": s,
        "throttle_settings": {p: round(s * w, 3) for p, w in weights.items()},
        "metrics": metrics,
        "junction_ratio": run["junction_ratio"],
        "n_sims": len(history),
        "history": history,
    }


def blunt_scenario(baseline_vol_m3: float, wards: dict | None = None, config: dict | None = None) -> dict:
    """BLUNT: the same throttle on every source main."""
    cfg = _cfg(config)
    res = solve_throttle({"weights": {p: 1.0 for p in cfg["source_mains"]},
                          "baseline_vol_m3": baseline_vol_m3, "wards": wards, "config": cfg})
    if not res["feasible"]:
        raise RuntimeError("BLUNT throttle search did not reach the target band")
    return res


def evaluate_candidate(payload: dict) -> dict:
    """Evaluate ONE FAIR candidate plan (one future Step Functions Map job).

    payload: {candidate_id, log_shape: {ward: u}, wards, baseline_vol_m3, config?}
    Per-ward throttle K_ward = s * 10**u_ward on every inlet pipe of that ward;
    s is solved so the total cut lands in the target band.
    """
    t0 = time.perf_counter()
    wards = payload["wards"]
    weights = {}
    for w, u in payload["log_shape"].items():
        for p in wards["wards"][w]["inlet_pipes"]:
            # A pipe that is an inlet of two wards keeps the stronger throttle.
            weights[p] = max(weights.get(p, 0.0), 10 ** u)
    res = solve_throttle({"weights": weights, "baseline_vol_m3": payload["baseline_vol_m3"],
                          "wards": wards, "config": payload.get("config")})
    res["candidate_id"] = payload["candidate_id"]
    res["log_shape"] = payload["log_shape"]
    if res["feasible"]:
        res["ward_settings"] = {w: round(res["scale"] * 10 ** u, 3) for w, u in payload["log_shape"].items()}
    res["runtime_s"] = round(time.perf_counter() - t0, 2)
    return res


def fair_search(baseline_vol_m3: float, wards: dict, config: dict | None = None, mapper=map,
                n_initial: int = 40, refine_rounds: int = 3, refine_top: int = 3, refine_children: int = 2,
                log_range: tuple = (-2.0, 2.0), seed: int = 7) -> dict:
    """FAIR: Latin-hypercube candidate plans + local refinement.

    mapper(fn, payloads) runs candidates (builtin map, a process pool, or later
    an AWS Step Functions Map state).
    """
    from scipy.stats import qmc

    ward_names = sorted(wards["wards"])
    lo, hi = log_range
    lhs = qmc.LatinHypercube(d=len(ward_names), seed=seed).random(n_initial)
    shapes = [lo + (hi - lo) * row for row in lhs]
    rng = np.random.default_rng(seed)

    def payloads(shape_list, start_id):
        return [{"candidate_id": start_id + i, "log_shape": {w: round(float(u), 4) for w, u in zip(ward_names, sh)},
                 "wards": wards, "baseline_vol_m3": baseline_vol_m3, "config": config}
                for i, sh in enumerate(shape_list)]

    evaluated = list(mapper(evaluate_candidate, payloads(shapes, 0)))
    sigmas = [0.5, 0.3, 0.15][:refine_rounds]
    for sigma in sigmas:
        best = sorted((c for c in evaluated if c["feasible"]), key=lambda c: fairness_key(c["metrics"]))[:refine_top]
        children = []
        for c in best:
            parent = np.array([c["log_shape"][w] for w in ward_names])
            for _ in range(refine_children):
                children.append(np.clip(parent + rng.normal(0, sigma, len(ward_names)), lo, hi))
        evaluated += list(mapper(evaluate_candidate, payloads(children, len(evaluated))))

    feasible = [c for c in evaluated if c["feasible"]]
    if not feasible:
        raise RuntimeError("No FAIR candidate reached the target band")
    best = min(feasible, key=lambda c: fairness_key(c["metrics"]))
    return {"best": best, "candidates": evaluated}


# --------------------------------------------------------------------------
# Leak-zone detection (SIMULATED leaks)
# --------------------------------------------------------------------------
def leak_detection(baseline_night: dict, wards: dict, config: dict | None = None, n_leaks: int = 3,
                   leak_area_m2: float = 0.001, seed: int = 11, meter_noise_sd: float = 0.02,
                   threshold_frac: float = 0.10) -> dict:
    """Inject leaks, compare ward night inflow (2-4 am bulk meters) with the no-leak baseline.

    Simulated bulk-meter error: each reading x (1 + N(0, meter_noise_sd)), seeded.
    Threshold: flag a ward if its night inflow exceeds baseline by > threshold_frac.
    With 2% meter error on both readings the difference has sd ~2.8% of baseline,
    so 10% is ~3.5 sd: a no-leak ward is flagged by noise far less than 1 time in 1000.
    """
    cfg = _cfg(config)
    rng = np.random.default_rng(seed)
    all_dj = sorted(j for w in wards["wards"].values() for j in w["demand_junctions"])
    leak_nodes = [str(j) for j in rng.choice(all_dj, size=n_leaks, replace=False)]
    leaks = [{"junction": j, "area_m2": leak_area_m2, "discharge_coeff": 0.75} for j in leak_nodes]

    boundaries = {w: info["boundary_links"] for w, info in wards["wards"].items()}
    run = run_scenario({"leaks": leaks, "ward_boundaries": boundaries}, cfg)

    leak_wards = {wards["junction_to_ward"][j] for j in leak_nodes}
    per_ward, tp, fp, fn = {}, [], [], []
    for w in sorted(boundaries):
        base = baseline_night[w] * (1 + rng.normal(0, meter_noise_sd))
        meas = run["ward_night_inflow_m3"][w] * (1 + rng.normal(0, meter_noise_sd))
        anomaly = (meas - base) / base
        flagged = anomaly > threshold_frac
        has_leak = w in leak_wards
        per_ward[w] = {
            "baseline_night_inflow_m3": round(base, 2),
            "leak_night_inflow_m3": round(meas, 2),
            "true_excess_m3": round(run["ward_night_inflow_m3"][w] - baseline_night[w], 2),
            "anomaly_pct": round(100 * anomaly, 2),
            "flagged": bool(flagged),
            "contains_leak": has_leak,
        }
        (tp if flagged and has_leak else fp if flagged else fn if has_leak else []).append(w)

    return {
        "label": "SIMULATED leaks and simulated bulk-meter noise on a sample network",
        "leaks": [{**l, "ward": wards["junction_to_ward"][l["junction"]]} for l in leaks],
        "night_window": "02:00-04:00, last simulated day",
        "threshold": f"flag if night inflow > baseline by {threshold_frac:.0%} (~3.5 sd of simulated "
                     f"{meter_noise_sd:.0%} meter noise on both readings)",
        "total_leak_volume_m3_per_day": round(run["leak_volume_m3"], 1),
        "per_ward": per_ward,
        "true_positives": tp,
        "false_positives": fp,
        "false_negatives": fn,
    }
