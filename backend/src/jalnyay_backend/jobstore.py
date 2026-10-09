"""Async job store shared by the API and worker Lambdas.

Job row (DynamoDB, table JOBS_TABLE, pk job_id, TTL attribute expires_at = +2 days):
  job_id, type, status (queued|running|done|failed), created_at, updated_at,
  input_key, result_key, result_bytes, runtime_s, cold_start, error
Payloads live in S3 (bucket BUCKET): jobs/<id>/input.json and jobs/<id>/result.json,
so neither the async Lambda event (size-limited) nor DynamoDB (400 KB items)
carries large inputs or results.
"""

from __future__ import annotations

import json
import os
import time
import traceback
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from . import validation

JOB_TTL_S = 2 * 24 * 3600
_clients: dict = {}
_COLD = True


def _client(name: str):
    import boto3  # in the Lambda runtime; imported lazily so tests can run without AWS

    if name not in _clients:
        _clients[name] = boto3.client(name) if name != "dynamodb" else boto3.resource("dynamodb")
    return _clients[name]


def bucket() -> str:
    return os.environ["BUCKET"]


def jobs_table():
    return _client("dynamodb").Table(os.environ["JOBS_TABLE"])


def _json_default(o):
    if isinstance(o, Decimal):
        return int(o) if o == o.to_integral_value() else float(o)
    raise TypeError(f"{type(o).__name__} is not JSON serialisable")


def dumps(obj) -> str:
    return json.dumps(obj, default=_json_default, separators=(",", ":"))


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def put_json(key: str, obj) -> int:
    body = dumps(obj).encode()
    _client("s3").put_object(Bucket=bucket(), Key=key, Body=body, ContentType="application/json")
    return len(body)


def get_json(key: str):
    return json.loads(_client("s3").get_object(Bucket=bucket(), Key=key)["Body"].read())


def presign(key: str, expires_s: int = 3600) -> str:
    return _client("s3").generate_presigned_url("get_object", Params={"Bucket": bucket(), "Key": key},
                                                ExpiresIn=expires_s)


def create_job(job_type: str, params: dict) -> dict:
    job_id = uuid.uuid4().hex
    input_key = f"jobs/{job_id}/input.json"
    put_json(input_key, {"type": job_type, "params": params})
    item = {"job_id": job_id, "type": job_type, "status": "queued", "created_at": now_iso(),
            "updated_at": now_iso(), "input_key": input_key, "expires_at": int(time.time()) + JOB_TTL_S}
    jobs_table().put_item(Item=item)
    return item


def get_job(job_id: str) -> dict | None:
    return jobs_table().get_item(Key={"job_id": job_id}).get("Item")


def update_job(job_id: str, **fields) -> None:
    fields["updated_at"] = now_iso()
    names = {f"#{k}": k for k in fields}
    values = {f":{k}": (Decimal(str(v)) if isinstance(v, float) else v) for k, v in fields.items()}
    jobs_table().update_item(Key={"job_id": job_id},
                             UpdateExpression="SET " + ", ".join(f"#{k} = :{k}" for k in fields),
                             ExpressionAttributeNames=names, ExpressionAttributeValues=values)


def run_job(job_id: str, runners: dict, cold_start: bool) -> dict:
    """Worker side: load input, run, store result, record status. Never raises
    (async invocations are configured with 0 retries; failures are recorded)."""
    job = get_job(job_id)
    if job is None:
        print(f"job {job_id} not found")
        return {"job_id": job_id, "status": "missing"}
    if job["status"] != "queued":  # idempotent if the event is ever delivered twice
        print(f"job {job_id} already {job['status']}")
        return {"job_id": job_id, "status": job["status"]}
    update_job(job_id, status="running", started_at=now_iso(), cold_start=cold_start)
    t0 = time.perf_counter()
    try:
        payload = get_json(job["input_key"])
        params = validation.validate(payload["type"], payload["params"])
        result = runners[payload["type"]](params, {"job_mode": True, "job_id": job_id})
        result_key = f"jobs/{job_id}/result.json"
        size = put_json(result_key, result)
        runtime = round(time.perf_counter() - t0, 2)
        update_job(job_id, status="done", result_key=result_key, result_bytes=size, runtime_s=runtime)
        print(f"job {job_id} done in {runtime} s (cold_start={cold_start}, {size} bytes)")
        return {"job_id": job_id, "status": "done", "runtime_s": runtime}
    except Exception as exc:  # recorded for the client; full trace in CloudWatch
        traceback.print_exc()
        update_job(job_id, status="failed", error=f"{type(exc).__name__}: {exc}"[:1000],
                   runtime_s=round(time.perf_counter() - t0, 2))
        return {"job_id": job_id, "status": "failed"}


def handle(event: dict, runners: dict) -> dict:
    """Lambda entry for workers.
    Job mode   {"job_id": ...}            (async invoke from the API)
    Direct mode {"type": ..., "params": {}} (sam local invoke / tests; no AWS calls)"""
    global _COLD
    cold, _COLD = _COLD, False
    if "job_id" in event:
        return run_job(event["job_id"], runners, cold)
    t0 = time.perf_counter()
    params = validation.validate(event["type"], event.get("params"))
    result = runners[event["type"]](params, {"job_mode": False})
    return {"result": result, "runtime_s": round(time.perf_counter() - t0, 2), "cold_start": cold}
