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


def test_distance_stub_is_marked():
    with pytest.raises(NotImplementedError):
        village.distance_matrix([{"lat": 18.0, "lon": 76.0}], {"distance_provider": "amazon_location"})
