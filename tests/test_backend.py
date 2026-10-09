"""Backend tests without AWS: validation, workers in direct mode, API routing
with an in-memory fake of the S3 / DynamoDB / Lambda clients."""

import json
from pathlib import Path

import pytest

from jalnyay_backend import api, jobstore, validation

REPO = Path(__file__).resolve().parents[1]


# ---------------- validation
def test_validation_defaults_and_errors():
    assert validation.validate("village_plan", {})["filling_points"] == ["Tuljapur"]
    assert validation.validate("village_plan", {"filling_points": ["Tuljapur", "Naldurg"]})["n_tankers"] == 6
    assert validation.validate("city_evaluate", None) == {"plan": "FAIR"}
    for job_type, params in [
        ("nope", {}),
        ("village_plan", {"filling_points": ["Naldurg"]}),  # base must be Tuljapur
        ("village_plan", {"n_tankers": 0}),
        ("village_plan", {"n_tankers": True}),
        ("village_plan", {"distance_provider": "amazon_location"}),  # billed: never per request
        ("city_evaluate", {"plan": "custom"}),
        ("city_evaluate", {"plan": "custom", "ward_settings": {"Ward A": -1}}),
        ("village_fleet", {"fleet_sizes": list(range(1, 31)), "fleet_probe_sizes": list(range(31, 61)),
                           "fleet_solver_time_limit_s": 10}),  # 120 solves x 10 s: over budget
        ("village_fraud", {}),
    ]:
        with pytest.raises(ValueError):
            validation.validate(job_type, params)


# ---------------- workers, direct mode (what `sam local invoke` runs)
def test_city_worker_fair_matches_part_b():
    from jalnyay_backend import city_worker

    out = city_worker.handler({"type": "city_evaluate", "params": {"plan": "FAIR"}}, None)
    local = json.loads((REPO / "outputs" / "partB_results.json").read_text())["scenarios"]["FAIR"]
    m = out["result"]["metrics"]
    assert m["underserved_count"] == local["underserved_count"] and m["dry_count"] == local["dry_count"]
    assert m["reduction_pct"] == pytest.approx(local["reduction_pct"], abs=0.05)
    assert m["min_ratio"] == pytest.approx(local["min_ratio"], abs=0.002)
    assert out["result"]["in_target_band"] and out["result"]["water_balance"]["ok"]


def test_city_worker_custom_ward_settings():
    from jalnyay_backend import city_worker

    out = city_worker.handler({"type": "city_evaluate",
                               "params": {"plan": "custom", "ward_settings": {"Ward A": 400, "Ward F": 100}}}, None)
    assert out["result"]["throttle_settings"]["137"] == 400.0  # Ward A inlet
    with pytest.raises(ValueError):
        city_worker.handler({"type": "city_evaluate", "params": {"plan": "custom", "ward_settings": {"Ward Z": 1}}}, None)


def test_village_workers_direct():
    from jalnyay_backend import village_worker

    naive_local = json.loads((REPO / "outputs" / "partC_results.json").read_text(encoding="utf-8"))["comparison"][0]
    out = village_worker.plan_handler({"type": "village_plan", "params": {
        "n_tankers": 6, "distance_provider": "haversine", "solver_time_limit_s": 3}}, None)["result"]
    assert out["comparison"][0] == naive_local  # naive plan is deterministic
    assert out["comparison"][1]["high_need_unserved"] == 0
    two = village_worker.plan_handler({"type": "village_plan", "params": {
        "filling_points": ["Tuljapur", "Naldurg"], "distance_provider": "haversine",
        "solver_time_limit_s": 3, "include_naive": False}}, None)["result"]
    assert two["fill_points"] == ["Tuljapur", "Naldurg"] and list(two["plans"]) == ["optimised"]
    fraud = village_worker.fraud_handler({"type": "village_fraud", "params": {"simulate": True}}, None)["result"]
    assert fraud["evaluation"]["precision"] == 1.0 and fraud["evaluation"]["recall"] == 1.0
    fleet = village_worker.fleet_handler({"type": "village_fleet", "params": {
        "fleet_sizes": [6, 8], "fleet_probe_sizes": [], "fleet_solver_time_limit_s": 1,
        "distance_provider": "haversine"}}, None)["result"]
    assert [r["n_tankers"] for r in fleet["sweep"]] == [6, 8]


# ---------------- API with in-memory fakes
class FakeAWS:
    def __init__(self):
        self.objects, self.jobs, self.invokes = {}, {}, []

    def install(self, monkeypatch):
        monkeypatch.setenv("BUCKET", "test-bucket")
        monkeypatch.setenv("JOBS_TABLE", "jobs")
        monkeypatch.setenv("VILLAGES_TABLE", "villages")
        for job_type, env in api.WORKERS.items():
            monkeypatch.setenv(env, f"fn-{job_type}")
        monkeypatch.setattr(jobstore, "put_json", lambda key, obj: self.objects.__setitem__(key, obj) or 10)
        monkeypatch.setattr(jobstore, "get_json", lambda key: self.objects[key])
        monkeypatch.setattr(jobstore, "presign", lambda key, expires_s=3600: f"https://signed/{key}")
        monkeypatch.setattr(jobstore, "jobs_table", lambda: self)
        monkeypatch.setattr(jobstore, "_client", lambda name: self)

    # DynamoDB table / Lambda client surface used by the code
    def put_item(self, Item):
        self.jobs[Item["job_id"]] = dict(Item)

    def get_item(self, Key):
        return {"Item": self.jobs[Key["job_id"]]} if Key["job_id"] in self.jobs else {}

    def invoke(self, **kw):
        self.invokes.append(kw)


def _call(route, body=None, path=None):
    return api.handler({"routeKey": route, "body": json.dumps(body) if body is not None else None,
                        "pathParameters": path or {}}, None)


def test_api_job_flow(monkeypatch):
    fake = FakeAWS()
    fake.install(monkeypatch)
    r = _call("POST /jobs", {"type": "village_plan", "params": {"n_tankers": 8}})
    assert r["statusCode"] == 202
    job_id = json.loads(r["body"])["job_id"]
    assert fake.invokes[0]["InvocationType"] == "Event" and fake.invokes[0]["FunctionName"] == "fn-village_plan"
    assert fake.objects[f"jobs/{job_id}/input.json"]["params"]["n_tankers"] == 8
    assert json.loads(_call("GET /jobs/{id}", path={"id": job_id})["body"])["status"] == "queued"

    fake.jobs[job_id].update(status="done", result_key="r.json", result_bytes=20)
    fake.objects["r.json"] = {"ok": 1}
    assert json.loads(_call("GET /jobs/{id}", path={"id": job_id})["body"])["result"] == {"ok": 1}
    fake.jobs[job_id]["result_bytes"] = api.INLINE_RESULT_MAX_BYTES + 1
    assert "result_url" in json.loads(_call("GET /jobs/{id}", path={"id": job_id})["body"])


def test_api_errors(monkeypatch):
    FakeAWS().install(monkeypatch)
    assert _call("POST /jobs", {"type": "bad"})["statusCode"] == 400
    assert api.handler({"routeKey": "POST /jobs", "body": "not json"}, None)["statusCode"] == 400
    assert _call("GET /jobs/{id}", path={"id": "../etc"})["statusCode"] == 400
    assert _call("GET /jobs/{id}", path={"id": "0" * 32})["statusCode"] == 404
    assert _call("DELETE /everything")["statusCode"] == 404


# ---------------- Part E additions: publish flag, warm-up
def test_publish_only_when_requested(monkeypatch):
    from jalnyay_backend import village_worker

    written = {}
    monkeypatch.setattr(jobstore, "get_json", lambda key: written.get(key, (_ for _ in ()).throw(KeyError(key))))
    monkeypatch.setattr(jobstore, "put_json", lambda key, obj: written.__setitem__(key, obj) or 1)
    base = {"n_tankers": 6, "distance_provider": "haversine", "solver_time_limit_s": 1, "include_naive": False}
    assert validation.validate("village_plan", base)["publish"] is False
    village_worker.plan(validation.validate("village_plan", base), {"job_mode": True, "job_id": "x"})
    assert village_worker.LATEST_KEY not in written  # experiments never touch the default view
    village_worker.plan(validation.validate("village_plan", {**base, "publish": True,
                                                             "filling_points": ["Tuljapur", "Naldurg"]}),
                        {"job_mode": True, "job_id": "y"})
    doc = written[village_worker.LATEST_KEY]
    assert list(doc["views"]) == ["Tuljapur+Naldurg"] and doc["views"]["Tuljapur+Naldurg"]["source"] == "job y"


def test_city_warmup():
    from jalnyay_backend import city_worker

    assert validation.validate("city_evaluate", {"warmup": True}) == {"warmup": True}
    out = city_worker.handler({"type": "city_evaluate", "params": {"warmup": True}}, None)["result"]
    assert out["warmup"] is True and "metrics" not in out
