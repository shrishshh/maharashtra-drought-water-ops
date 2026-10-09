"""Seed the deployed backend with precomputed results and village data.

Uploads to S3 (private bucket from the stack outputs):
  networks/Net3.inp                         EPA Net3 sample network (not a real Maharashtra network)
  precomputed/city_scenarios.json           Part B BLUNT / FAIR / leaks + simulated wards (slim)
  precomputed/partB_results.json, wards.json
  precomputed/partC_results.json, villages.json
  precomputed/village_plan_latest.json      website default view, ROAD distances (cached Amazon Location
                                            matrix): views for Tuljapur and Tuljapur+Naldurg (same shape as
                                            village_plan job results) + road-based fleet sweep + fraud summary
  precomputed/*.png                         Part A/B/C charts
  precomputed/route_matrix_tuljapur.json    only if built (scripts/build_route_matrix.py) and not expired
Writes the 32 Tuljapur villages (+ need scores) to DynamoDB jalnyay-villages.

Usage (after deploy + scripts/stack_outputs.py):  .venv\\Scripts\\python.exe scripts\\seed.py
"""

import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # repo root: city/, village/
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend" / "src"))  # jalnyay_backend

import boto3

from stack_outputs import load
from village import engine as village_engine

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "outputs"
CITY_LABEL = "Representative sample network (EPA Net3), not a real Maharashtra network - wards and leaks SIMULATED"
PNGS = ["partA_maps.png", "partB_compare.png", "partB_leaks.png", "partC_routes.png", "partC_routes_2fill.png",
        "partC_naive_vs_opt.png", "partC_fleet.png", "partC_fraud.png"]


def city_scenarios(part_b: dict, wards: dict) -> dict:
    return {
        "label": CITY_LABEL,
        "baseline": part_b["baseline"],
        "scenarios": part_b["scenarios"],
        "leaks": part_b["leaks"],
        "wards": {w: {"centroid": v["centroid"], "inlet_pipes": v["inlet_pipes"],
                      "demand_junctions": v["demand_junctions"]} for w, v in wards["wards"].items()},
        "fair_search": {k: v for k, v in part_b["fair_search"].items() if k != "candidates"},
        "timing_s": part_b["timing_s"],
        "source": "precomputed Part B (local run)",
    }


MATRIX_FILE = REPO / "data" / "village" / "route_matrix_tuljapur.json"


def load_matrix() -> dict:
    """The cached Amazon Location matrix; refuse to seed road-based views without a valid one."""
    if not MATRIX_FILE.exists():
        raise SystemExit("No route matrix: run scripts/build_route_matrix.py --yes --upload first")
    matrix = json.loads(MATRIX_FILE.read_text(encoding="utf-8"))
    if matrix.get("expires_at", "") <= time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()):
        raise SystemExit(f"Route matrix expired {matrix.get('expires_at')} (30-day cache limit): rebuild it")
    return matrix


def _road_view(job: tuple) -> dict:
    """One default-view plan, computed with the worker's own code (identical result shape)."""
    matrix, fill_points = job
    from jalnyay_backend import validation, village_worker

    village_engine.set_distance_cache(matrix)
    village_worker._CACHE_STATUS = "seed: local copy of the cached Amazon Location matrix"
    params = validation.validate("village_plan", {"n_tankers": 6, "filling_points": fill_points})
    return village_worker.plan(params, {"job_mode": False})


def village_plan_latest(part_c: dict, matrix: dict) -> dict:
    """Website default view on ROAD distances: both filling-point views + fleet sweep."""
    from jalnyay_backend import village_worker

    configs = [["Tuljapur"], ["Tuljapur", "Naldurg"]]
    workers = max(1, (os.cpu_count() or 2) - 1)
    with ProcessPoolExecutor(2) as pool:
        views = list(pool.map(_road_view, [(matrix, fps) for fps in configs]))
    villages = json.loads((REPO / "data" / "village" / "villages.json").read_text(encoding="utf-8"))
    with ProcessPoolExecutor(workers, initializer=village_engine.set_distance_cache, initargs=(matrix,)) as pool:
        fleet = village_engine.plan_fleet({**villages, "fill_points": villages["fill_points"][:1]},
                                          {"distance_provider": "cache"}, mapper=pool.map)
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return {
        "label": village_worker.LABEL,
        "default_view": "Tuljapur",
        "views": {village_worker.view_key(v["fill_points"]): {**v, "source": "seed (road distances)",
                                                               "updated_at": now} for v in views},
        "fleet": {**fleet, "distance_provider": "cached Amazon Location road matrix", "fill_points": ["Tuljapur"]},
        "fraud": {"evaluation": part_c["fraud"]["evaluation"], "robustness": part_c["fraud"]["robustness"]},
        "source": "seed.py (local run of the worker code on the cached road matrix)",
        "updated_at": now,
    }


def main():
    cfg = load()
    bucket, region = cfg["BucketName"], cfg["region"]
    s3 = boto3.client("s3", region_name=region)

    def put(key, body: bytes, ctype):
        s3.put_object(Bucket=bucket, Key=key, Body=body, ContentType=ctype)
        print(f"  s3://{bucket}/{key}  ({len(body):,} bytes)")

    def put_json(key, obj):
        put(key, json.dumps(obj, ensure_ascii=False).encode(), "application/json")

    part_b = json.loads((OUT / "partB_results.json").read_text())
    wards = json.loads((OUT / "wards.json").read_text())
    part_c = json.loads((OUT / "partC_results.json").read_text(encoding="utf-8"))
    villages = json.loads((REPO / "data" / "village" / "villages.json").read_text(encoding="utf-8"))
    matrix = load_matrix()
    print("Computing the road-based default view (2 plans x 20 s + fleet sweep)...")
    latest = village_plan_latest(part_c, matrix)
    for key, v in latest["views"].items():
        opt = v["comparison"][-1]
        print(f"  {key:<18} {opt['litres_delivered']:,} L, {opt['villages_served']} villages, {opt['total_km']} km")
    print(f"  fleet: (a) {latest['fleet']['min_tankers_every_high_need_one_load']} tankers, "
          f"(b) {latest['fleet']['min_tankers_full_human_need']} tankers")

    print("S3 uploads:")
    put("networks/Net3.inp", (REPO / "data" / "networks" / "Net3.inp").read_bytes(), "text/plain")
    put_json("precomputed/city_scenarios.json", city_scenarios(part_b, wards))
    put_json("precomputed/partB_results.json", part_b)
    put_json("precomputed/wards.json", wards)
    put_json("precomputed/partC_results.json", part_c)
    put_json("precomputed/villages.json", villages)
    put_json("precomputed/village_plan_latest.json", latest)
    for png in PNGS:
        put(f"precomputed/{png}", (OUT / png).read_bytes(), "image/png")

    put_json(cfg.get("RouteMatrixKey", "precomputed/route_matrix_tuljapur.json"), matrix)

    need = {r["id"]: r for r in village_engine.need_assessment(villages)["ranked"]}
    table = boto3.resource("dynamodb", region_name=region).Table(cfg["VillagesTableName"])
    with table.batch_writer(overwrite_by_pkeys=["village_id"]) as batch:
        for v in villages["villages"]:
            r = need[v["id"]]
            item = {**{k: v[k] for k in v if k != "id"}, "village_id": v["id"],
                    **{k: r[k] for k in ("rank", "daily_need_l", "urgency", "need_score", "loads_needed",
                                         "eligible", "high_need")},
                    "data_note": "population: Census 2011; *_sim fields SIMULATED"}
            batch.put_item(Item=json.loads(json.dumps(item), parse_float=Decimal))
    print(f"DynamoDB {cfg['VillagesTableName']}: {len(villages['villages'])} villages written")


if __name__ == "__main__":
    main()
