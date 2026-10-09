"""Export static geometry/demo data that the website bundles (never changes at runtime).

frontend/src/data/net3.json        EPA Net3 sample network: node x/y (not lat/lon), links,
                                   SIMULATED ward outlines (padded convex hulls) + label points
frontend/src/data/village_static.json  filling points (OSM towns, ASSUMED filling points) and the
                                   SIMULATED fraud demo (GPS traces, claims, detector report)

Usage:  .venv\\Scripts\\python.exe scripts\\export_frontend_data.py
"""

import json
import sys
from pathlib import Path

import numpy as np
from scipy.spatial import ConvexHull

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from city import engine as city_engine  # noqa: E402
from village import engine as village_engine  # noqa: E402

OUT = REPO / "frontend" / "src" / "data"


def net3() -> dict:
    wn = city_engine.build_network()
    wards = json.loads((REPO / "outputs" / "wards.json").read_text())
    nodes = {}
    for name, node in wn.nodes():
        x, y = node.coordinates
        nodes[name] = {"x": round(x, 2), "y": round(y, 2), "type": node.node_type}
    links = [{"id": name, "a": l.start_node_name, "b": l.end_node_name, "type": l.link_type}
             for name, l in wn.links()]
    xy = np.array([[n["x"], n["y"]] for n in nodes.values()])
    pad = 0.02 * np.ptp(xy, axis=0).max()
    ring = pad * np.array([[np.cos(a), np.sin(a)] for a in np.linspace(0, 2 * np.pi, 12, endpoint=False)])
    outlines = {}
    for w, info in wards["wards"].items():
        pts = np.array([[nodes[n]["x"], nodes[n]["y"]] for n in info["demand_junctions"] + info["other_junctions"]])
        padded = (pts[:, None, :] + ring[None, :, :]).reshape(-1, 2)
        hull = padded[ConvexHull(padded).vertices]
        outlines[w] = {"polygon": [[round(float(x), 1), round(float(y), 1)] for x, y in hull],
                       "label": [round(float(v), 1) for v in pts.mean(axis=0)],
                       "inlet_pipes": info["inlet_pipes"]}
    return {
        "label": "Representative sample network (EPA Net3), not a real Maharashtra network; wards SIMULATED",
        "bounds": {"minX": float(xy[:, 0].min()), "maxX": float(xy[:, 0].max()),
                   "minY": float(xy[:, 1].min()), "maxY": float(xy[:, 1].max())},
        "nodes": nodes, "links": links, "wards": outlines,
        "junction_to_ward": wards["junction_to_ward"],
    }


def village_static() -> dict:
    data = json.loads((REPO / "data" / "village" / "villages.json").read_text(encoding="utf-8"))
    gps = json.loads((REPO / "outputs" / "partC_gps_sim.json").read_text(encoding="utf-8"))
    report = village_engine.detect_fraud({**gps, "villages": data["villages"], "fill_points": data["fill_points"]})
    return {
        "fill_points": data["fill_points"],
        "fraud_demo": {
            "label": "SIMULATED GPS traces (1/min, 30 m noise, signal gaps) and trip claims with 5 injected frauds",
            "claims": gps["claims"], "traces": gps["traces"], "gaps": gps["gaps"], "report": report,
            "evaluation": village_engine.evaluate_fraud(report, gps["claims"]),
            "shift_start": "07:00",
        },
    }


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for name, obj in [("net3.json", net3()), ("village_static.json", village_static())]:
        (OUT / name).write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        print(f"{OUT / name}: {(OUT / name).stat().st_size / 1024:.0f} KB")
