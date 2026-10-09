"""Seed the deployed backend with precomputed results and village data.

Uploads to S3 (private bucket from the stack outputs):
  networks/Net3.inp                         EPA Net3 sample network (not a real Maharashtra network)
  precomputed/city_scenarios.json           Part B BLUNT / FAIR / leaks + simulated wards (slim)
  precomputed/partB_results.json, wards.json
  precomputed/partC_results.json, villages.json
  precomputed/village_plan_latest.json      Part C plan, same shape as a village_plan job result
  precomputed/*.png                         Part A/B/C charts
  precomputed/route_matrix_tuljapur.json    only if built (scripts/build_route_matrix.py) and not expired
Writes the 32 Tuljapur villages (+ need scores) to DynamoDB jalnyay-villages.

Usage (after deploy + scripts/stack_outputs.py):  .venv\\Scripts\\python.exe scripts\\seed.py
"""

import json
import time
from decimal import Decimal
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # repo root: city/, village/

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


def village_plan_latest(part_c: dict) -> dict:
    """Part C results in the same shape as a village_plan job result (+ extras for the website)."""
    plans = part_c["plans"]
    return {
        "label": part_c["label"],
        "params": {"n_tankers": part_c["config"]["n_tankers"], "eligible_only_dry": True,
                   "filling_points": ["Tuljapur"], "distance_provider": "haversine"},
        "fill_points": ["Tuljapur"],
        "distance_provider": plans["optimised"]["distance_provider"],
        "need": part_c["need"],
        "plans": {"naive": plans["naive"], "optimised": plans["optimised"]},
        "comparison": part_c["comparison"][:2],
        "alternatives": {"two_fill_points": {"plan": plans["optimised_2_fill_points"],
                                             "comparison": part_c["comparison"][2]}},
        "fleet": part_c["fleet"],
        "fraud": {"evaluation": part_c["fraud"]["evaluation"], "robustness": part_c["fraud"]["robustness"]},
        "source": "precomputed Part C (local run)",
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
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

    print("S3 uploads:")
    put("networks/Net3.inp", (REPO / "data" / "networks" / "Net3.inp").read_bytes(), "text/plain")
    put_json("precomputed/city_scenarios.json", city_scenarios(part_b, wards))
    put_json("precomputed/partB_results.json", part_b)
    put_json("precomputed/wards.json", wards)
    put_json("precomputed/partC_results.json", part_c)
    put_json("precomputed/villages.json", villages)
    put_json("precomputed/village_plan_latest.json", village_plan_latest(part_c))
    for png in PNGS:
        put(f"precomputed/{png}", (OUT / png).read_bytes(), "image/png")

    matrix_file = REPO / "data" / "village" / "route_matrix_tuljapur.json"
    if matrix_file.exists():
        matrix = json.loads(matrix_file.read_text(encoding="utf-8"))
        if matrix.get("expires_at", "") > time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()):
            put_json(cfg.get("RouteMatrixKey", "precomputed/route_matrix_tuljapur.json"), matrix)
        else:
            print(f"  SKIPPED route matrix: expired {matrix.get('expires_at')} (30-day cache limit); rebuild it")
    else:
        print("  (no route matrix built yet - village_plan uses haversine until scripts/build_route_matrix.py runs)")

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
