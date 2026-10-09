"""HTTP API handler (API Gateway HTTP API, payload format 2.0).

POST /jobs {type, params}  -> 202 {job_id}; worker Lambda invoked asynchronously
GET  /jobs/{id}            -> status (+ result inline, or a presigned URL if large)
GET  /city/scenarios       -> precomputed BLUNT / FAIR / leak results (Part B)
GET  /village/villages     -> Tuljapur villages + need scores (DynamoDB)
GET  /village/plan/latest  -> latest village plan (seeded from Part C, then updated by plan jobs)

API Gateway's integration timeout (~30 s) is shorter than our runs (6-150 s),
hence the async job pattern. All data is SIMULATED or sample data (see labels).
"""

from __future__ import annotations

import base64
import json
import os
import re

from . import jobstore, validation

WORKERS = {
    "city_evaluate": "WORKER_CITY_EVALUATE",
    "village_plan": "WORKER_VILLAGE_PLAN",
    "village_fleet": "WORKER_VILLAGE_FLEET",
    "village_fraud": "WORKER_VILLAGE_FRAUD",
}
MAX_BODY_BYTES = 2_000_000
INLINE_RESULT_MAX_BYTES = 4_000_000  # Lambda responses are capped at 6 MB
JOB_ID_RE = re.compile(r"^[0-9a-f]{32}$")
CITY_IMAGES = {"compare": "precomputed/partB_compare.png", "leaks": "precomputed/partB_leaks.png"}
VILLAGE_IMAGES = {"routes": "precomputed/partC_routes.png", "routes_2fill": "precomputed/partC_routes_2fill.png",
                  "naive_vs_opt": "precomputed/partC_naive_vs_opt.png", "fleet": "precomputed/partC_fleet.png",
                  "fraud": "precomputed/partC_fraud.png"}


def _resp(status: int, body, cache_s: int = 0) -> dict:
    headers = {"content-type": "application/json"}
    if cache_s:
        headers["cache-control"] = f"public, max-age={cache_s}"
    return {"statusCode": status, "headers": headers, "body": jobstore.dumps(body)}


def _error(status: int, message: str) -> dict:
    return _resp(status, {"error": message})


def _images(keys: dict) -> dict:
    return {name: jobstore.presign(key) for name, key in keys.items()}


def post_job(event: dict) -> dict:
    raw = event.get("body") or ""
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode()
    if len(raw) > MAX_BODY_BYTES:
        return _error(413, f"body larger than {MAX_BODY_BYTES} bytes")
    try:
        body = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return _error(400, "body must be JSON")
    if not isinstance(body, dict):
        return _error(400, "body must be a JSON object {type, params}")
    try:
        params = validation.validate(body.get("type"), body.get("params"))
    except ValueError as exc:
        return _error(400, str(exc))
    job = jobstore.create_job(body["type"], params)
    jobstore._client("lambda").invoke(FunctionName=os.environ[WORKERS[body["type"]]], InvocationType="Event",
                                      Payload=json.dumps({"job_id": job["job_id"]}).encode())
    return _resp(202, {"job_id": job["job_id"], "type": job["type"], "status": "queued",
                       "poll": f"/jobs/{job['job_id']}"})


def get_job(job_id: str) -> dict:
    if not JOB_ID_RE.match(job_id or ""):
        return _error(400, "invalid job id")
    job = jobstore.get_job(job_id)
    if job is None:
        return _error(404, "job not found (jobs expire after 2 days)")
    out = {k: job[k] for k in ("job_id", "type", "status", "created_at", "updated_at", "started_at",
                               "runtime_s", "cold_start", "error", "result_bytes") if k in job}
    if job["status"] == "done":
        if int(job.get("result_bytes", 0)) <= INLINE_RESULT_MAX_BYTES:
            out["result"] = jobstore.get_json(job["result_key"])
        else:
            out["result_url"] = jobstore.presign(job["result_key"])
    return _resp(200, out)


def city_scenarios() -> dict:
    data = jobstore.get_json("precomputed/city_scenarios.json")
    data["images"] = _images(CITY_IMAGES)
    return _resp(200, data, cache_s=60)


def village_villages() -> dict:
    table = jobstore._client("dynamodb").Table(os.environ["VILLAGES_TABLE"])
    items, kwargs = [], {}
    while True:
        page = table.scan(**kwargs)
        items += page["Items"]
        if "LastEvaluatedKey" not in page:
            break
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    items.sort(key=lambda v: int(v.get("rank", 10**6)))
    return _resp(200, {"label": "Tuljapur taluka, Dharashiv: real OSM locations + Census 2011 population; "
                                "fields ending in _sim are SIMULATED", "count": len(items), "villages": items},
                 cache_s=60)


def village_plan_latest() -> dict:
    data = jobstore.get_json("precomputed/village_plan_latest.json")
    data["images"] = _images(VILLAGE_IMAGES)
    return _resp(200, data, cache_s=30)


def handler(event, context):
    route = event.get("routeKey", "")
    try:
        if route == "POST /jobs":
            return post_job(event)
        if route == "GET /jobs/{id}":
            return get_job((event.get("pathParameters") or {}).get("id", ""))
        if route == "GET /city/scenarios":
            return city_scenarios()
        if route == "GET /village/villages":
            return village_villages()
        if route == "GET /village/plan/latest":
            return village_plan_latest()
        return _error(404, f"no route {route}")
    except Exception as exc:  # log the detail, return a generic message
        import traceback

        traceback.print_exc()
        return _error(500, f"internal error ({type(exc).__name__})")
