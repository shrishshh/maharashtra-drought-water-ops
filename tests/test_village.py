"""Tests for the Village engine (Tuljapur taluka; livestock/trips/GPS SIMULATED).

Route and fraud tests read outputs from village/partC_run.py; run it first.
"""

import json
from pathlib import Path

import pytest

from village import engine as village

REPO = Path(__file__).resolve().parents[1]

DATA = REPO / "data" / "village" / "villages.json"
RESULTS = REPO / "outputs" / "partC_results.json"
GPS = REPO / "outputs" / "partC_gps_sim.json"
CFG = village._cfg(None)


@pytest.fixture(scope="module")
def data():
    return json.loads(DATA.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def results():
    if not RESULTS.exists():
        pytest.skip("run village/partC_run.py first")
    return json.loads(RESULTS.read_text(encoding="utf-8"))


def all_routes(results):
    for name, plan in results["plans"].items():
        for r in plan["routes"]:
            yield name, r


def test_need_scores_positive(data):
    ranked = village.need_assessment(data)["ranked"]
    assert len(ranked) == len(data["villages"])
    assert all(r["need_score"] > 0 and r["daily_need_l"] > 0 and r["loads_needed"] >= 1 for r in ranked)
    assert [r["need_score"] for r in ranked] == sorted((r["need_score"] for r in ranked), reverse=True)


def test_routes_respect_capacity_and_hours(results):
    cap, max_min = CFG["tanker_capacity_l"], CFG["max_hours"] * 60
    for name, r in all_routes(results):
        assert r["end_min"] <= max_min, (name, r["tanker"])
        load = 0
        for e in r["events"]:
            load = 0 if e["type"] == "fill" else load + e["litres"]
            assert load <= cap, (name, r["tanker"], e)


def test_no_delivery_before_filling(results):
    for name, r in all_routes(results):
        if r["events"]:
            assert r["events"][0]["type"] == "fill", (name, r["tanker"])
            assert r["events"][0]["arrive_min"] == 0
        filled = False
        for e in r["events"]:
            filled = filled or e["type"] == "fill"
            if e["type"] == "deliver":
                assert filled, (name, r["tanker"], e)


def test_timeline_is_consistent(results):
    for _, r in all_routes(results):
        t = 0
        for e in r["events"]:
            assert e["arrive_min"] >= t
            assert e["depart_min"] - e["arrive_min"] == (CFG["fill_min"] if e["type"] == "fill" else CFG["unload_min"])
            t = e["depart_min"]


def test_every_fraud_type_caught(data):
    if not GPS.exists():
        pytest.skip("run village/partC_run.py first")
    gps = json.loads(GPS.read_text(encoding="utf-8"))
    report = village.detect_fraud({**gps, "villages": data["villages"], "fill_point": data["fill_point"]})
    ev = village.evaluate_fraud(report, gps["claims"])
    assert set(ev["types_caught"]) == {"a_no_visit", "b_short_stop", "c_no_refill", "d_impossible_speed"}
    reasons = {f["claim_id"]: " ".join(f["reasons"]) for f in report["flagged"]}
    for c in gps["claims"]:
        if c["injected_fraud"]:
            assert c["injected_fraud"][0] + " " in reasons.get(c["claim_id"], ""), c


def test_only_eligible_villages_get_water(data, results):
    eligible = {v["id"] for v in data["villages"] if v["source_dry_sim"]}
    for name, plan in results["plans"].items():
        assert set(plan["litres_by_village"]) <= eligible, name
        assert {u["village_id"] for u in plan["dropped_units"]} <= eligible, name


def test_fills_happen_at_a_filling_point(data, results):
    names = {f["name"] for f in data["fill_points"]}
    for _, r in all_routes(results):
        assert all(e["place"] in names for e in r["events"] if e["type"] == "fill")
        assert not r["events"] or r["events"][0]["place"] == data["fill_points"][0]["name"]  # day starts at base


def test_fleet_sweep(results):
    fleet = results["fleet"]
    by_n = {r["n_tankers"]: r for r in fleet["sweep"]}
    assert set(fleet["sweep_sizes"]) <= set(by_n) and min(fleet["sweep_sizes"]) == 6 and max(fleet["sweep_sizes"]) == 40
    assert by_n[40]["need_covered_pct"] > by_n[6]["need_covered_pct"]
    a, b = fleet["min_tankers_every_high_need_one_load"], fleet["min_tankers_full_human_need"]
    assert a is not None and by_n[a]["high_need_unserved"] == 0
    assert b is None or by_n[b]["human_need_covered_pct"] == 100.0


PTS = [{"lat": 18.0, "lon": 76.0}, {"lat": 18.1, "lon": 76.1}, {"lat": 17.9, "lon": 76.2}]


def test_distance_cache_lookup_and_fallback():
    cache = {"points": PTS, "provider": "test", "km": [[0, 1, 2], [1, 0, 3], [2, 3, 0]],
             "minutes": [[0, 10, 20], [10, 0, 30], [20, 30, 0]]}
    try:
        village.set_distance_cache(cache)
        sub = village.distance_matrix([PTS[2], PTS[0]], {"distance_provider": "cache"})
        assert sub["km"] == [[0, 2], [2, 0]] and sub["minutes"] == [[0, 20], [20, 0]]
        missing = village.distance_matrix([PTS[0], {"lat": 1.0, "lon": 1.0}], {"distance_provider": "cache"})
        assert "fallback" in missing["provider"]  # haversine, and it says so
        village.set_distance_cache({**cache, "expires_at": "2000-01-01T00:00:00Z"})
        expired = village.distance_matrix(PTS, {"distance_provider": "cache"})
        assert "expired" in expired["provider"]  # never use route results past the 30-day cache limit
    finally:
        village.set_distance_cache(None)


def test_amazon_location_matrix_parsing(monkeypatch):
    """No network: a fake geo-routes client checks the request and returns a canned matrix."""
    calls = []

    class FakeClient:
        def calculate_route_matrix(self, **kw):
            calls.append(kw)
            n = len(kw["Origins"])
            rows = [[{"Distance": 1000 * (i + j), "Duration": 60 * (i + j) + 1} for j in range(n)] for i in range(n)]
            rows[0][2] = {"Error": "NoMatch"}
            return {"RouteMatrix": rows, "ErrorCount": 1}

    import boto3
    monkeypatch.setattr(boto3, "client", lambda service, region_name=None: FakeClient())
    m = village.distance_matrix(PTS, {"distance_provider": "amazon_location"})
    assert len(calls) == 1 and "BoundingBox" in calls[0]["RoutingBoundary"]["Geometry"]
    assert calls[0]["Origins"][0]["Position"] == [76.0, 18.0]  # [lon, lat]
    assert m["km"][1][2] == 3.0 and m["minutes"][1][2] == 4  # 181 s -> ceil 4 min
    assert m["failed_cells"] == 1 and m["km"][0][2] > 0  # error cell filled from haversine
