"""Village engine: drought tanker planner for one taluka.

DATA: real village locations (OSM) and Census 2011 population for Tuljapur
taluka, Dharashiv; livestock, source status, tanker history, requests, GPS
traces and trip claims are SIMULATED.

Public functions take plain dicts / JSON-compatible values and return
JSON-serialisable dicts (future Lambda handlers):

  need_assessment(payload, config)   need score + tanker loads, ranked
  distance_matrix(points, config)    pluggable: haversine x detour now, Amazon Location later
  plan_routes(payload, config)       OR-Tools multi-trip tanker routing with prize-collecting drops
  naive_plan(payload, config)        first-come-first-served baseline
  compare_plans(plans, ranked)       km, tanker-hours, litres, high-need villages unserved
  simulate_gps / build_claims        SIMULATED GPS traces and trip claims (+ injected frauds)
  detect_fraud(payload, config)      rule-based claim checks with reason strings
  evaluate_fraud(report, claims)     precision / recall vs injected frauds
"""

from __future__ import annotations

import math
import time

import numpy as np

DEFAULT_CONFIG: dict = {
    # --- Water norms. MUST be checked against the Maharashtra drought tanker GR. ---
    # 20 L/person/day: the government tanker norm as reported in the press
    # (e.g. Business Standard / PTI, Mar 2019); GR number not yet verified.
    "human_lpcd": 20,
    # Livestock: ASSUMPTIONS (no GR source found yet): 35 L/day per large
    # animal (cattle/buffalo), 10 L/day per small animal (sheep/goat).
    "large_animal_lpd": 35,
    "small_animal_lpd": 10,
    # --- Urgency ---
    "urgency_source_dry": 1.5,  # x1.5 if the village's own source is dry
    "urgency_per_day_waiting": 0.05,  # +5% per day since last tanker ...
    "urgency_days_cap": 10,  # ... capped at 10 days (x1.5)
    # --- Fleet ---
    "n_tankers": 6,
    "tanker_capacity_l": 10_000,
    "max_hours": 10,
    "fill_min": 20,
    "unload_min": 20,
    "shift_start": "07:00",
    # --- Distances ---
    "distance_provider": "haversine",  # "amazon_location" in Part D
    "detour_factor": 1.3,
    "speed_kmh": 30,
    # --- Solver ---
    "solver_time_limit_s": 30,
    "max_trips_per_tanker": 10,
    "penalty_scale": 100,  # drop penalty = scale x need_score x max(decay^k, floor) for the k-th load
    "penalty_decay": 0.6,  # later loads to the same village are worth less (first load matters most)
    "penalty_floor": 0.1,
    "refill_arc_cost_m": 200,  # small cost so unnecessary refill visits are avoided
    "high_need_quantile": 0.75,  # top 25% by need score = "high-need"
    # --- Fraud detection ---
    "gps_interval_min": 1,
    "gps_noise_m": 10,
    "visit_radius_m": 300,
    "claim_window_min": 60,  # look for the GPS visit within +/- this of the claimed time
    "min_dwell_frac": 0.75,  # stop must last >= 75% of unload time
    "fill_dwell_frac": 0.5,  # a fill-point stop >= 50% of fill time counts as a refill
    "max_plausible_kmh": 60,
}


def _cfg(config: dict | None) -> dict:
    return {**DEFAULT_CONFIG, **(config or {})}


# --------------------------------------------------------------------------
# Need score
# --------------------------------------------------------------------------
def need_assessment(payload: dict, config: dict | None = None) -> dict:
    """payload: {villages: [...]} as in data/village/villages.json -> ranked list."""
    cfg = _cfg(config)
    rows = []
    for v in payload["villages"]:
        need = (v["population"] * cfg["human_lpcd"] + v["large_animals_sim"] * cfg["large_animal_lpd"]
                + v["small_animals_sim"] * cfg["small_animal_lpd"])
        urgency = ((cfg["urgency_source_dry"] if v["source_dry_sim"] else 1.0)
                   * (1 + cfg["urgency_per_day_waiting"] * min(v["days_since_last_tanker_sim"], cfg["urgency_days_cap"])))
        rows.append({
            "id": v["id"], "name": v["name"], "population": v["population"],
            "daily_need_l": int(need), "urgency": round(urgency, 3),
            "need_score": round(need * urgency, 1),
            "loads_needed": math.ceil(need / cfg["tanker_capacity_l"]),
            "source_dry_sim": v["source_dry_sim"], "days_since_last_tanker_sim": v["days_since_last_tanker_sim"],
            "request_date_sim": v["request_date_sim"],
        })
    rows.sort(key=lambda r: -r["need_score"])
    cut = np.quantile([r["need_score"] for r in rows], cfg["high_need_quantile"])
    for i, r in enumerate(rows):
        r["rank"] = i + 1
        r["high_need"] = bool(r["need_score"] >= cut)
    return {"ranked": rows, "total_need_l": sum(r["daily_need_l"] for r in rows),
            "total_loads_needed": sum(r["loads_needed"] for r in rows)}


# --------------------------------------------------------------------------
# Distances (pluggable)
# --------------------------------------------------------------------------
def _haversine_km(a: dict, b: dict) -> float:
    r = 6371.0088
    p1, p2 = math.radians(a["lat"]), math.radians(b["lat"])
    dp, dl = p2 - p1, math.radians(b["lon"] - a["lon"])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def _haversine_matrix(points: list[dict], cfg: dict) -> dict:
    km = [[_haversine_km(a, b) * cfg["detour_factor"] for b in points] for a in points]
    minutes = [[math.ceil(d / cfg["speed_kmh"] * 60) for d in row] for row in km]
    return {"provider": f"haversine x {cfg['detour_factor']} detour, {cfg['speed_kmh']} km/h",
            "km": [[round(d, 3) for d in row] for row in km], "minutes": minutes}


def _amazon_location_matrix(points: list[dict], cfg: dict) -> dict:
    # ------------------------------------------------------------------
    # STUB - Part D: Amazon Location Service route matrix.
    # Plan: boto3 client("geo-routes", region "ap-south-1").calculate_route_matrix(
    #   Origins=[{"Position": [lon, lat]}...], Destinations=[...same...],
    #   TravelMode="Truck", RoutingBoundary={"Unbounded": True})
    # -> RouteMatrix[i][j]["Distance"] (m) / ["Duration"] (s); convert to
    # km / ceil(minutes) and return the same dict shape as _haversine_matrix.
    # Large inputs need batching to the API's origin/destination limits.
    # ------------------------------------------------------------------
    raise NotImplementedError("Amazon Location route matrix is plugged in during Part D")


DISTANCE_PROVIDERS = {"haversine": _haversine_matrix, "amazon_location": _amazon_location_matrix}


def distance_matrix(points: list[dict], config: dict | None = None) -> dict:
    """points: [{lat, lon}, ...] -> {provider, km[i][j], minutes[i][j]} (minutes are integers, rounded up)."""
    cfg = _cfg(config)
    return DISTANCE_PROVIDERS[cfg["distance_provider"]](points, cfg)


def _locations(payload: dict) -> list[dict]:
    """Location 0 = filling point, 1..n = villages in payload order."""
    fp = payload["fill_point"]
    return [{"lat": fp["lat"], "lon": fp["lon"], "name": fp["name"]}] + [
        {"lat": v["lat"], "lon": v["lon"], "name": v["name"], "id": v["id"]} for v in payload["villages"]]


def _units(payload: dict, ranked: list[dict], cfg: dict) -> list[dict]:
    """Split each village's need into tanker-load units (last unit may be partial)."""
    loc_of = {v["id"]: i + 1 for i, v in enumerate(payload["villages"])}
    cap, units = cfg["tanker_capacity_l"], []
    for r in ranked:
        remaining = r["daily_need_l"]
        for k in range(r["loads_needed"]):
            litres = min(cap, remaining)
            remaining -= litres
            units.append({"unit_id": f"{r['id']}-{k + 1}", "village_id": r["id"], "loc": loc_of[r["id"]],
                          "k": k, "litres": int(litres), "need_score": r["need_score"]})
    return units


# --------------------------------------------------------------------------
# Route bookkeeping shared by optimised + naive plans
# --------------------------------------------------------------------------
def _build_route(tanker: int, stops: list[dict], dm: dict, locs: list[dict], cfg: dict) -> dict:
    """stops: [{"type": "fill"} | {"type": "deliver", loc, litres, unit_id, village_id}] (no start/end).
    Normalises (start with a fill, collapse repeated fills, drop trailing fills),
    computes times/km, and asserts capacity + working hours."""
    seq = [{"type": "fill"}]
    for s in stops:
        if s["type"] == "fill" and seq[-1]["type"] == "fill":
            continue
        seq.append(s)
    while len(seq) > 1 and seq[-1]["type"] == "fill":
        seq.pop()

    events, t, km, prev, load, trips = [], 0, 0.0, 0, 0, 0
    cap = cfg["tanker_capacity_l"]
    for s in seq:
        loc = 0 if s["type"] == "fill" else s["loc"]
        t += dm["minutes"][prev][loc]
        km += dm["km"][prev][loc]
        if s["type"] == "fill":
            events.append({"type": "fill", "loc": 0, "place": locs[0]["name"], "arrive_min": t,
                           "depart_min": t + cfg["fill_min"]})
            t += cfg["fill_min"]
            load, trips = 0, trips + 1
        else:
            load += s["litres"]
            if load > cap:
                raise AssertionError(f"tanker {tanker}: trip load {load} L exceeds capacity {cap} L")
            events.append({"type": "deliver", "loc": loc, "village_id": s["village_id"], "place": locs[loc]["name"],
                           "unit_id": s["unit_id"], "litres": s["litres"], "trip": trips,
                           "arrive_min": t, "depart_min": t + cfg["unload_min"]})
            t += cfg["unload_min"]
        prev = loc
    has_delivery = any(e["type"] == "deliver" for e in events)
    if has_delivery:  # return to the filling point at the end of the day
        t += dm["minutes"][prev][0]
        km += dm["km"][prev][0]
    else:
        events, t, km, trips = [], 0, 0.0, 0
    if t > cfg["max_hours"] * 60:
        raise AssertionError(f"tanker {tanker}: {t} min exceeds {cfg['max_hours']} h")
    return {
        "tanker": f"T{tanker + 1}", "events": events, "trips": trips,
        "km": round(km, 2), "hours": round(t / 60, 3), "end_min": t,
        "litres": int(sum(e["litres"] for e in events if e["type"] == "deliver")),
    }


def _plan_summary(name: str, routes: list[dict], units: list[dict], runtime: float, extra: dict | None = None) -> dict:
    served = {e["unit_id"] for r in routes for e in r["events"] if e["type"] == "deliver"}
    dropped = [u for u in units if u["unit_id"] not in served]
    by_village: dict = {}
    for r in routes:
        for e in r["events"]:
            if e["type"] == "deliver":
                by_village[e["village_id"]] = by_village.get(e["village_id"], 0) + e["litres"]
    return {
        "plan": name, "routes": routes, "runtime_s": round(runtime, 2),
        "litres_by_village": by_village,
        "dropped_units": [{"unit_id": u["unit_id"], "village_id": u["village_id"], "litres": u["litres"]} for u in dropped],
        **(extra or {}),
    }


# --------------------------------------------------------------------------
# Optimised plan (OR-Tools)
# --------------------------------------------------------------------------
def plan_routes(payload: dict, config: dict | None = None) -> dict:
    """payload: {villages, fill_point, ranked?} -> optimised one-day plan.

    Model: node 0 = filling point (start/end; tanker fills before leaving),
    R optional refill nodes at the filling point (penalty 0), and one optional
    node per tanker-load unit. A "Load" dimension counts litres delivered since
    the last fill (+litres at units, -capacity at refills with slack), so a
    trip never exceeds capacity. A "Time" dimension (service + travel) caps the
    working day. Dropping unit k of a village costs
    penalty_scale x need_score x max(decay^k, floor), so high-need villages
    (and their first loads) are served first. Objective: km + drop penalties.
    """
    from ortools.constraint_solver import pywrapcp, routing_enums_pb2

    cfg = _cfg(config)
    t0 = time.perf_counter()
    ranked = payload.get("ranked") or need_assessment(payload, cfg)["ranked"]
    locs = _locations(payload)
    dm = distance_matrix(locs, cfg)
    units = _units(payload, ranked, cfg)
    cap = cfg["tanker_capacity_l"]
    n_veh = cfg["n_tankers"]
    n_refill = n_veh * cfg["max_trips_per_tanker"]

    # node -> (loc, service_min, demand_l)
    nodes = [(0, cfg["fill_min"], 0)] + [(0, cfg["fill_min"], -cap)] * n_refill + [
        (u["loc"], cfg["unload_min"], u["litres"]) for u in units]
    first_unit = 1 + n_refill
    dist_m = [[int(round(d * 1000)) for d in row] for row in dm["km"]]

    manager = pywrapcp.RoutingIndexManager(len(nodes), n_veh, 0)
    routing = pywrapcp.RoutingModel(manager)

    def dist_cb(i, j):
        a, b = manager.IndexToNode(i), manager.IndexToNode(j)
        extra = cfg["refill_arc_cost_m"] if 1 <= b < first_unit else 0
        return dist_m[nodes[a][0]][nodes[b][0]] + extra

    def time_cb(i, j):
        a, b = manager.IndexToNode(i), manager.IndexToNode(j)
        return nodes[a][1] + dm["minutes"][nodes[a][0]][nodes[b][0]]

    def demand_cb(i):
        return nodes[manager.IndexToNode(i)][2]

    routing.SetArcCostEvaluatorOfAllVehicles(routing.RegisterTransitCallback(dist_cb))
    routing.AddDimension(routing.RegisterTransitCallback(time_cb), 0, cfg["max_hours"] * 60, True, "Time")
    routing.AddDimension(routing.RegisterUnaryTransitCallback(demand_cb), cap, cap, True, "Load")
    load = routing.GetDimensionOrDie("Load")

    for n in range(1, first_unit):
        idx = manager.NodeToIndex(n)
        load.SlackVar(idx).SetRange(0, cap)
        routing.AddDisjunction([idx], 0)
    for n, u in enumerate(units, start=first_unit):
        idx = manager.NodeToIndex(n)
        load.SlackVar(idx).SetValue(0)
        weight = max(cfg["penalty_decay"] ** u["k"], cfg["penalty_floor"])
        routing.AddDisjunction([idx], int(cfg["penalty_scale"] * u["need_score"] * weight))

    params = pywrapcp.DefaultRoutingSearchParameters()
    params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PARALLEL_CHEAPEST_INSERTION
    params.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    params.time_limit.FromSeconds(cfg["solver_time_limit_s"])
    solution = routing.SolveWithParameters(params)
    if solution is None:
        raise RuntimeError("OR-Tools found no solution")

    routes = []
    for v in range(n_veh):
        stops, idx = [], solution.Value(routing.NextVar(routing.Start(v)))
        while not routing.IsEnd(idx):
            n = manager.IndexToNode(idx)
            if n < first_unit:
                stops.append({"type": "fill"})
            else:
                u = units[n - first_unit]
                stops.append({"type": "deliver", "loc": u["loc"], "litres": u["litres"],
                              "unit_id": u["unit_id"], "village_id": u["village_id"]})
            idx = solution.Value(routing.NextVar(idx))
        routes.append(_build_route(v, stops, dm, locs, cfg))

    return _plan_summary("optimised (OR-Tools)", routes, units, time.perf_counter() - t0,
                         {"solver_objective": solution.ObjectiveValue(), "distance_provider": dm["provider"],
                          "solver_time_limit_s": cfg["solver_time_limit_s"]})


# --------------------------------------------------------------------------
# Naive baseline: first come, first served
# --------------------------------------------------------------------------
def naive_plan(payload: dict, config: dict | None = None) -> dict:
    """Requests served strictly in request-date order (ties: village id); each
    load is its own trip (fill -> village -> back). The earliest-free tanker
    takes the next load; a tanker that cannot fit that trip in its day stops.
    No skipping ahead in the queue."""
    cfg = _cfg(config)
    t0 = time.perf_counter()
    ranked = payload.get("ranked") or need_assessment(payload, cfg)["ranked"]
    locs = _locations(payload)
    dm = distance_matrix(locs, cfg)
    order = {r["id"]: (r["request_date_sim"], r["id"]) for r in ranked}
    units = sorted(_units(payload, ranked, cfg), key=lambda u: (order[u["village_id"]], u["k"]))

    free_at = {k: 0 for k in range(cfg["n_tankers"])}
    stops = {k: [] for k in free_at}
    limit = cfg["max_hours"] * 60
    for u in units:
        placed = False
        for k in sorted(free_at, key=lambda k: (free_at[k], k)):
            trip = cfg["fill_min"] + dm["minutes"][0][u["loc"]] + cfg["unload_min"]
            back = dm["minutes"][u["loc"]][0]
            if free_at[k] + trip + back <= limit:
                stops[k] += [{"type": "fill"}, {"type": "deliver", "loc": u["loc"], "litres": u["litres"],
                                                "unit_id": u["unit_id"], "village_id": u["village_id"]}]
                free_at[k] += trip + back
                placed = True
                break
            del free_at[k]  # this tanker's day is over
        if not placed and not free_at:
            break
    routes = [_build_route(k, stops[k], dm, locs, cfg) for k in range(cfg["n_tankers"])]
    return _plan_summary("naive (first come, first served)", routes, units, time.perf_counter() - t0)


def compare_plans(plans: list[dict], ranked: list[dict]) -> list[dict]:
    high = {r["id"] for r in ranked if r["high_need"]}
    need = {r["id"]: r["daily_need_l"] for r in ranked}
    rows = []
    for p in plans:
        got = p["litres_by_village"]
        rows.append({
            "plan": p["plan"],
            "total_km": round(sum(r["km"] for r in p["routes"]), 1),
            "tanker_hours": round(sum(r["hours"] for r in p["routes"]), 2),
            "litres_delivered": int(sum(got.values())),
            "trips": sum(r["trips"] for r in p["routes"]),
            "villages_served": sum(1 for v in need if got.get(v, 0) > 0),
            "high_need_villages": len(high),
            "high_need_unserved": sum(1 for v in high if got.get(v, 0) == 0),
            "litres_to_high_need": int(sum(got.get(v, 0) for v in high)),
            "need_covered_pct": round(100 * sum(got.values()) / sum(need.values()), 1),
            "litres_per_km": round(sum(got.values()) / max(sum(r["km"] for r in p["routes"]), 1e-9), 1),
        })
    return rows


# --------------------------------------------------------------------------
# SIMULATED GPS traces + trip claims, with injected frauds
# --------------------------------------------------------------------------
def _offset(lat: float, lon: float, north_m: float, east_m: float) -> tuple[float, float]:
    return lat + north_m / 111_320, lon + east_m / (111_320 * math.cos(math.radians(lat)))


def _itinerary(route: dict, locs: list[dict], dm: dict, cfg: dict) -> list[dict]:
    """Route events -> list of legs: {kind: stay|move, t0, t1, frm, to} with (lat, lon) endpoints."""
    legs, prev, t = [], (locs[0]["lat"], locs[0]["lon"]), 0
    for e in route["events"]:
        here = (locs[e["loc"]]["lat"], locs[e["loc"]]["lon"])
        if e["arrive_min"] > t:
            legs.append({"kind": "move", "t0": t, "t1": e["arrive_min"], "frm": prev, "to": here})
        legs.append({"kind": "stay", "t0": e["arrive_min"], "t1": e["depart_min"], "frm": here, "to": here})
        prev, t = here, e["depart_min"]
    if route["events"]:
        legs.append({"kind": "move", "t0": t, "t1": route["end_min"], "frm": prev,
                     "to": (locs[0]["lat"], locs[0]["lon"])})
    return legs


def _sample(legs: list[dict], cfg: dict, rng) -> list[list[float]]:
    pts = []
    if not legs:
        return pts
    for t in range(0, int(legs[-1]["t1"]) + 1, cfg["gps_interval_min"]):
        leg = next((l for l in legs if l["t0"] <= t <= l["t1"]), legs[-1])
        f = 0.0 if leg["t1"] == leg["t0"] else (t - leg["t0"]) / (leg["t1"] - leg["t0"])
        lat = leg["frm"][0] + f * (leg["to"][0] - leg["frm"][0])
        lon = leg["frm"][1] + f * (leg["to"][1] - leg["frm"][1])
        lat, lon = _offset(lat, lon, rng.normal(0, cfg["gps_noise_m"]), rng.normal(0, cfg["gps_noise_m"]))
        pts.append([t, round(lat, 6), round(lon, 6)])
    return pts


def simulate_gps_and_claims(plan: dict, payload: dict, config: dict | None = None, seed: int = 99) -> dict:
    """SIMULATED: 1-min GPS traces for the plan + one delivery claim per stop,
    then inject 5 fraudulent claims (ground truth kept in each claim's
    "injected_fraud" field, which the detector never reads):
      a  x2  claim at a village the tanker never went near
      b      real stop, but the tanker left after 4 min (GPS altered)
      c      second delivery without going back to the filling point (GPS altered)
      d      claim at a far village minutes after a real claim (impossible speed)
    """
    cfg = _cfg(config)
    rng = np.random.default_rng(seed)
    locs = _locations(payload)
    dm = distance_matrix(locs, cfg)
    routes = [r for r in plan["routes"] if r["events"]]
    itineraries = {r["tanker"]: _itinerary(r, locs, dm, cfg) for r in routes}

    claims = []
    for r in routes:
        for e in r["events"]:
            if e["type"] == "deliver":
                claims.append({"tanker": r["tanker"], "village_id": e["village_id"], "litres": e["litres"],
                               "claimed_min": e["arrive_min"] + 2, "injected_fraud": None})

    def deliveries(r):
        return [e for e in r["events"] if e["type"] == "deliver"]

    # (b) short stop: leave after 4 min, park ~1.5 km away until the planned departure.
    rb = routes[0]
    eb = deliveries(rb)[0]
    it = itineraries[rb["tanker"]]
    i = next(i for i, l in enumerate(it) if l["kind"] == "stay" and l["t0"] == eb["arrive_min"])
    leg = it[i]
    park = _offset(*leg["frm"], 1000, 1100)
    t_mid = leg["t0"] + 4
    it[i:i + 1] = [
        {"kind": "stay", "t0": leg["t0"], "t1": t_mid, "frm": leg["frm"], "to": leg["frm"]},
        {"kind": "move", "t0": t_mid, "t1": t_mid + 3, "frm": leg["frm"], "to": park},
        {"kind": "stay", "t0": t_mid + 3, "t1": leg["t1"], "frm": park, "to": park}]
    for c in claims:
        if c["tanker"] == rb["tanker"] and c["claimed_min"] == eb["arrive_min"] + 2:
            c["injected_fraud"] = "b_short_stop"

    # (c) skip the refill between two consecutive full-load trips: drive A -> B directly, wait at B.
    rc = next(r for r in routes[1:] if len([e for e in deliveries(r) if e["litres"] == cfg["tanker_capacity_l"]]) >= 2)
    evs = rc["events"]
    i_fill = next(i for i, e in enumerate(evs) if i > 1 and e["type"] == "fill" and evs[i - 1]["type"] == "deliver"
                  and i + 1 < len(evs) and evs[i + 1]["type"] == "deliver")
    ea, eb2 = evs[i_fill - 1], evs[i_fill + 1]
    a_pt = (locs[ea["loc"]]["lat"], locs[ea["loc"]]["lon"])
    b_pt = (locs[eb2["loc"]]["lat"], locs[eb2["loc"]]["lon"])
    direct = dm["minutes"][ea["loc"]][eb2["loc"]]
    it = itineraries[rc["tanker"]]
    keep = [l for l in it if l["t1"] <= ea["depart_min"] or l["t0"] >= eb2["arrive_min"]]
    keep += [{"kind": "move", "t0": ea["depart_min"], "t1": ea["depart_min"] + direct, "frm": a_pt, "to": b_pt},
             {"kind": "stay", "t0": ea["depart_min"] + direct, "t1": eb2["arrive_min"], "frm": b_pt, "to": b_pt}]
    itineraries[rc["tanker"]] = sorted(keep, key=lambda l: (l["t0"], l["t1"]))
    for c in claims:
        if c["tanker"] == rc["tanker"] and c["claimed_min"] == eb2["arrive_min"] + 2:
            c["injected_fraud"] = "c_no_refill"

    traces = {t: _sample(legs, cfg, rng) for t, legs in itineraries.items()}

    # (a) x2: phantom deliveries to villages far from the tanker's trace.
    def far_village(tanker, t_min, min_km=8.0):
        pts = [p for p in traces[tanker] if abs(p[0] - t_min) <= cfg["claim_window_min"] + 5]
        for v in payload["villages"]:
            if all(_haversine_km({"lat": p[1], "lon": p[2]}, v) > min_km for p in pts):
                return v
        raise RuntimeError("no far village found")

    for r, frac in ((routes[2 % len(routes)], 0.35), (routes[3 % len(routes)], 0.7)):
        t_claim = int(r["end_min"] * frac)
        v = far_village(r["tanker"], t_claim)
        claims.append({"tanker": r["tanker"], "village_id": v["id"], "litres": cfg["tanker_capacity_l"],
                       "claimed_min": t_claim, "injected_fraud": "a_no_visit"})

    # (d) impossible speed: a claim 8 min after a real claim, at a village >= 15 km away.
    rd = routes[4 % len(routes)]
    ed = deliveries(rd)[1]
    origin = locs[ed["loc"]]
    v = max(payload["villages"], key=lambda x: _haversine_km(origin, x))
    if _haversine_km(origin, v) < 15:
        raise RuntimeError("no village far enough for the speed fraud")
    claims.append({"tanker": rd["tanker"], "village_id": v["id"], "litres": cfg["tanker_capacity_l"],
                   "claimed_min": ed["arrive_min"] + 2 + 8, "injected_fraud": "d_impossible_speed"})

    claims.sort(key=lambda c: (c["tanker"], c["claimed_min"]))
    for i, c in enumerate(claims):
        c["claim_id"] = f"C{i + 1:03d}"
    return {"label": "SIMULATED GPS traces and trip claims", "traces": traces, "claims": claims}


# --------------------------------------------------------------------------
# Fraud detection (rule-based)
# --------------------------------------------------------------------------
def _clock(cfg: dict, minutes: float) -> str:
    h, m = map(int, cfg["shift_start"].split(":"))
    total = h * 60 + m + int(round(minutes))
    return f"{total // 60 % 24:02d}:{total % 60:02d}"


def _runs_within(trace: list, place: dict, radius_m: float, t_lo: float, t_hi: float) -> list[tuple[int, int]]:
    """Contiguous runs (t_start, t_end) of GPS points within radius of place, in [t_lo, t_hi]."""
    runs, start, last = [], None, None
    for t, lat, lon in trace:
        if t < t_lo or t > t_hi:
            continue
        inside = _haversine_km({"lat": lat, "lon": lon}, place) * 1000 <= radius_m
        if inside and start is None:
            start = t
        if inside:
            last = t
        if not inside and start is not None:
            runs.append((start, last))
            start = None
    if start is not None:
        runs.append((start, last))
    return runs


def detect_fraud(payload: dict, config: dict | None = None) -> dict:
    """payload: {claims, traces, villages, fill_point} -> per-claim flags with reasons.

    Rules (per tanker, claims in time order):
      a no_gps_visit     no GPS point within visit_radius_m of the village in +/- claim_window_min
      b short_stop       longest stay there < min_dwell_frac x unload time
      c no_refill        GPS-verified litres since the last fill-point stop
                         (>= fill_dwell_frac x fill time) exceed tanker capacity
      d impossible_speed road distance (haversine x detour) from the previous
                         GPS-verified claim / time gap > max_plausible_kmh
    Rules c and d only count GPS-verified claims, so one phantom claim does
    not cast suspicion on the honest claims around it.
    """
    cfg = _cfg(config)
    vill = {v["id"]: v for v in payload["villages"]}
    fp = payload["fill_point"]
    win, r_m = cfg["claim_window_min"], cfg["visit_radius_m"]
    min_dwell = cfg["min_dwell_frac"] * cfg["unload_min"]
    cap = cfg["tanker_capacity_l"]
    flags = []
    by_tanker: dict = {}
    for c in payload["claims"]:
        by_tanker.setdefault(c["tanker"], []).append(c)

    for tanker, cl in by_tanker.items():
        trace = payload["traces"].get(tanker, [])
        fills = [r for r in _runs_within(trace, fp, r_m, -1, 1e9) if r[1] - r[0] + 1 >= cfg["fill_dwell_frac"] * cfg["fill_min"]]
        litres_since_fill, last_verified, last_fill_end = 0, None, -1
        for c in sorted(cl, key=lambda x: x["claimed_min"]):
            v = vill[c["village_id"]]
            t = c["claimed_min"]
            reasons = []
            runs = _runs_within(trace, v, r_m, t - win, t + win)
            if not runs:
                reasons.append(f"a no_gps_visit: no GPS point within {r_m} m of {v['name']} between "
                               f"{_clock(cfg, t - win)} and {_clock(cfg, t + win)}")
            else:
                dwell = max(e - s + 1 for s, e in runs)
                if dwell < min_dwell:
                    reasons.append(f"b short_stop: longest stop at {v['name']} was {dwell} min "
                                   f"(< {min_dwell:.0f} min = {cfg['min_dwell_frac']:.0%} of unload time)")
                # refills completed since the previous verified claim reset the tank
                new_fills = [f for f in fills if last_fill_end < f[1] <= t]
                if new_fills:
                    litres_since_fill, last_fill_end = 0, new_fills[-1][1]
                litres_since_fill += c["litres"]
                if litres_since_fill > cap:
                    reasons.append(f"c no_refill: {litres_since_fill:,} L claimed since the last filling-point stop "
                                   f"(capacity {cap:,} L)")
            if last_verified is not None:
                pv = vill[last_verified["village_id"]]
                km = _haversine_km(pv, v) * cfg["detour_factor"]
                gap_h = max(t - last_verified["claimed_min"], 1) / 60
                if km / gap_h > cfg["max_plausible_kmh"]:
                    reasons.append(f"d impossible_speed: {km:.1f} km from {pv['name']} ({last_verified['claim_id']}) "
                                   f"in {gap_h * 60:.0f} min = {km / gap_h:.0f} km/h (> {cfg['max_plausible_kmh']} km/h)")
            if runs and not any(r.startswith("d ") for r in reasons):
                last_verified = c
            if reasons:
                flags.append({"claim_id": c["claim_id"], "tanker": tanker, "village": v["name"],
                              "claimed_time": _clock(cfg, t), "reasons": reasons})
    return {"n_claims": len(payload["claims"]), "flagged": flags}


def evaluate_fraud(report: dict, claims: list[dict]) -> dict:
    flagged = {f["claim_id"] for f in report["flagged"]}
    fraud = {c["claim_id"]: c["injected_fraud"] for c in claims if c["injected_fraud"]}
    tp = flagged & set(fraud)
    caught_types = sorted({fraud[c] for c in tp})
    return {
        "injected": len(fraud), "flagged": len(flagged), "true_positives": len(tp),
        "false_positives": sorted(flagged - set(fraud)), "missed": sorted(set(fraud) - flagged),
        "precision": round(len(tp) / len(flagged), 3) if flagged else None,
        "recall": round(len(tp) / len(fraud), 3) if fraud else None,
        "types_caught": caught_types,
        "per_injected": [{"claim_id": c, "type": t, "flagged": c in flagged} for c, t in sorted(fraud.items())],
    }
