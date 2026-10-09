"""Build the website and deploy it to AWS Amplify Hosting (manual deploy, no GitHub connection).

Steps: npm run build (VITE_API_URL from backend/deploy_outputs.json) -> zip dist/ ->
amplify create-deployment -> PUT the zip to the presigned URL -> start-deployment -> wait.

The Amplify app is created only with --create-app (asks nothing else; run it once,
after approval). Its id and URL are stored in backend/deploy_outputs.json (gitignored).

Usage:
  .venv\\Scripts\\python.exe scripts\\deploy_frontend.py --create-app   # first time: creates app + branch, then deploys
  .venv\\Scripts\\python.exe scripts\\deploy_frontend.py                # redeploy
"""

import argparse
import io
import json
import os
import subprocess
import time
import urllib.request
import zipfile
from pathlib import Path

import boto3

REPO = Path(__file__).resolve().parents[1]
FRONT = REPO / "frontend"
OUTPUTS = REPO / "backend" / "deploy_outputs.json"
APP_NAME, BRANCH, REGION = "jalnyay", "main", "ap-south-1"


def build(api_url: str) -> None:
    env = {**os.environ, "VITE_API_URL": api_url}
    subprocess.run("npm run build", cwd=FRONT, env=env, check=True, shell=True)


def zip_dist() -> bytes:
    buf = io.BytesIO()
    dist = FRONT / "dist"
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in dist.rglob("*"):
            if f.is_file():
                z.write(f, f.relative_to(dist).as_posix())
    return buf.getvalue()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--create-app", action="store_true", help="create the Amplify app + branch (billable resource)")
    args = ap.parse_args()
    outputs = json.loads(OUTPUTS.read_text())
    amp = boto3.client("amplify", region_name=REGION)

    app_id = outputs.get("AmplifyAppId")
    if not app_id:
        if not args.create_app:
            raise SystemExit("No Amplify app yet: re-run with --create-app (after approval)")
        app = amp.create_app(name=APP_NAME, platform="WEB", description="JalNyay website (manual deploys)",
                             tags={"project": "jalnyay"})["app"]
        app_id = app["appId"]
        amp.create_branch(appId=app_id, branchName=BRANCH, stage="PRODUCTION", tags={"project": "jalnyay"})
        outputs.update(AmplifyAppId=app_id, AmplifyUrl=f"https://{BRANCH}.{app['defaultDomain']}")
        OUTPUTS.write_text(json.dumps(outputs, indent=2))
        print(f"Created Amplify app {app_id} with branch {BRANCH}")

    build(outputs["ApiUrl"])
    body = zip_dist()
    dep = amp.create_deployment(appId=app_id, branchName=BRANCH)
    req = urllib.request.Request(dep["zipUploadUrl"], data=body, method="PUT", headers={"Content-Type": "application/zip"})
    urllib.request.urlopen(req, timeout=120).read()
    amp.start_deployment(appId=app_id, branchName=BRANCH, jobId=dep["jobId"])
    print(f"Uploaded {len(body) / 1024:.0f} KB; deployment job {dep['jobId']} started")
    for _ in range(90):
        status = amp.get_job(appId=app_id, branchName=BRANCH, jobId=dep["jobId"])["job"]["summary"]["status"]
        if status in ("SUCCEED", "FAILED", "CANCELLED"):
            break
        time.sleep(4)
    print(f"Deployment {status}: {outputs['AmplifyUrl']}")
    if status != "SUCCEED":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
