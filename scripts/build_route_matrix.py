"""Build the Tuljapur road-distance matrix with Amazon Location Service (Routes V2,
geo-routes CalculateRouteMatrix, ap-south-1) and cache it for village_plan.

BILLED: one request of N x N routes (N = villages + filling points = 34 -> 1,156
routes, Core pricing bucket for Truck). Without --yes this script only prints
the estimate and exits; it makes no AWS calls.

AWS Service Terms 82.4(a)(i): route results may be cached for up to 30 days.
The matrix carries expires_at = created + 30 days; the engine refuses expired
matrices, the S3 copy has a 30-day lifecycle rule, and the local copy is gitignored.

Usage:
  .venv\\Scripts\\python.exe scripts\\build_route_matrix.py              # estimate only
  .venv\\Scripts\\python.exe scripts\\build_route_matrix.py --yes        # call the API, save locally
  .venv\\Scripts\\python.exe scripts\\build_route_matrix.py --yes --upload  # ... and upload to the stack bucket
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # repo root: city/, village/

from village import engine

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "data" / "village" / "villages.json"
OUT = REPO / "data" / "village" / "route_matrix_tuljapur.json"
FREE_TIER_ROUTES = 10_000  # per month, first 3 months (Core bucket), per the AWS pricing page


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--yes", action="store_true", help="really call the (billed) API")
    ap.add_argument("--upload", action="store_true", help="upload to the deployed stack's S3 bucket")
    ap.add_argument("--price-per-1000", type=float, default=0.50,
                    help="USD per 1,000 Core routes used for the estimate (check the AWS pricing page)")
    args = ap.parse_args()

    payload = json.loads(DATA.read_text(encoding="utf-8"))
    n = len(payload["villages"]) + len(payload["fill_points"])
    routes = n * n
    print(f"Points: {len(payload['villages'])} villages + {len(payload['fill_points'])} filling points = {n}")
    print(f"Routes billed: {n} x {n} = {routes:,} (one request, RoutingBoundary bounding box, TravelMode Truck)")
    print(f"Estimate: {routes:,} / 1,000 x ${args.price_per_1000:.2f} = ${routes / 1000 * args.price_per_1000:.2f} "
          f"(ASSUMED price - confirm on the pricing page); $0 if within the {FREE_TIER_ROUTES:,} routes/month free tier")
    if not args.yes:
        print("\nNo API call made. Re-run with --yes to build the matrix.")
        return

    cache = engine.build_distance_cache(payload, {"distance_provider": "amazon_location"})
    OUT.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    print(f"\n{cache['provider']}; pricing bucket: {cache.get('pricing_bucket')}")
    print(f"Saved {OUT} (gitignored; expires {cache['expires_at']})")

    hav = engine.distance_matrix(cache["points"], {"distance_provider": "haversine"})
    ratios = [cache["km"][i][j] / (hav["km"][i][j] / 1.3) for i in range(n) for j in range(n)
              if i != j and hav["km"][i][j] > 0]
    ratios.sort()
    print(f"Road km / straight-line km: median {ratios[len(ratios) // 2]:.2f} "
          f"(haversine model assumed 1.30), range {ratios[0]:.2f}-{ratios[-1]:.2f}")

    if args.upload:
        import boto3

        from stack_outputs import load

        cfg = load()
        key = cfg.get("RouteMatrixKey", "precomputed/route_matrix_tuljapur.json")
        boto3.client("s3", region_name=cfg["region"]).put_object(
            Bucket=cfg["BucketName"], Key=key, Body=OUT.read_bytes(), ContentType="application/json")
        print(f"Uploaded s3://{cfg['BucketName']}/{key} (lifecycle: deleted after 30 days)")


if __name__ == "__main__":
    main()
