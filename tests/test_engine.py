"""Tests for the City engine (EPA Net3 sample network, SIMULATED).

Scenario/ward tests read outputs from city/partB_run.py and re-simulate the
chosen settings; run that script first.
"""

import importlib.util
import json
from pathlib import Path

import pytest
import wntr

REPO = Path(__file__).resolve().parents[1]
# Load by path under a unique name: city/ and village/ both have an engine.py.
_spec = importlib.util.spec_from_file_location("city_engine", REPO / "city" / "engine.py")
engine = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(engine)

RESULTS = REPO / "outputs" / "partB_results.json"
WARDS = REPO / "outputs" / "wards.json"


def test_water_balance_holds_baseline():
    _, _, balance = engine.simulate({})
    assert balance["ok"]
    assert balance["max_continuity_residual_m3s"] < 1e-6


def test_water_balance_holds_with_leak_and_throttle():
    spec = {"throttles": {"60": 400, "101": 400}, "leaks": [{"junction": "15", "area_m2": 0.001}]}
    _, _, balance = engine.simulate(spec)
    assert balance["ok"]


def test_water_balance_catches_epanet_phantom_tanks():
    """The Part A failure: EPANET empty tanks keep 'supplying' water with a frozen level."""
    wn = engine.build_network()
    for name in list(wn.control_name_list):
        wn.remove_control(name)
    for _, pump in wn.pumps():
        pump.initial_status = wntr.network.LinkStatus.Closed
    wn.get_link("330").initial_status = wntr.network.LinkStatus.Closed  # River gravity bypass
    cache = REPO / "outputs" / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    results = wntr.sim.EpanetSimulator(wn).run_sim(file_prefix=str(cache / "test_epanet"))
    with pytest.raises(engine.WaterBalanceError, match="phantom water"):
        engine.water_balance_check(wn, results)


def _results():
    if not RESULTS.exists():
        pytest.skip("run city/partB_run.py first")
    return json.loads(RESULTS.read_text())


@pytest.mark.parametrize("scenario", ["BLUNT", "FAIR"])
def test_scenario_within_band(scenario):
    res = _results()
    lo, hi = res["config"]["reduction_band"]
    sc = res["scenarios"][scenario]
    assert 100 * lo <= sc["reduction_pct"] <= 100 * hi
    # Re-simulate the saved throttle settings: the cut must reproduce.
    base = engine.run_scenario({})
    run = engine.run_scenario({"throttles": sc["throttle_settings"]})
    reduction = 1 - run["vol_delivered_m3"] / base["vol_delivered_m3"]
    assert lo <= reduction <= hi


def test_wards_cover_every_demand_junction():
    if not WARDS.exists():
        pytest.skip("run city/partB_run.py first")
    wards = json.loads(WARDS.read_text())
    wn = engine.build_network()
    demand = set(engine.demand_junctions(wn))
    assigned = [j for w in wards["wards"].values() for j in w["demand_junctions"]]
    assert set(assigned) == demand
    assert len(assigned) == len(demand)  # each junction in exactly one ward
    assert len(wards["wards"]) == 6
    assert all(w["inlet_pipes"] for w in wards["wards"].values())
