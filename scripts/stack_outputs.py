"""Read the deployed stack's outputs into backend/deploy_outputs.json (gitignored) and print them.

Usage (after `sam deploy`):  .venv\\Scripts\\python.exe scripts\\stack_outputs.py
Read-only: one CloudFormation DescribeStacks call.
"""

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "backend" / "deploy_outputs.json"
STACK, REGION = "jalnyay", "ap-south-1"


def fetch() -> dict:
    import boto3

    stack = boto3.client("cloudformation", region_name=REGION).describe_stacks(StackName=STACK)["Stacks"][0]
    outputs = {o["OutputKey"]: o["OutputValue"] for o in stack.get("Outputs", [])}
    return {"stack": STACK, "region": REGION, "status": stack["StackStatus"], **outputs}


def load() -> dict:
    """Cached outputs (run this script once after each deploy)."""
    if not OUT.exists():
        raise SystemExit(f"{OUT} missing: run scripts/stack_outputs.py after deploying")
    return json.loads(OUT.read_text())


if __name__ == "__main__":
    out = fetch()
    OUT.write_text(json.dumps(out, indent=2))
    for k, v in out.items():
        print(f"{k:<26} {v}")
    print(f"\nSaved {OUT} (gitignored)")
