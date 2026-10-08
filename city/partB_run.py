"""Part B: BLUNT vs FAIR 10% cut + leak-zone detection on SIMULATED wards.

NETWORK: representative sample network (EPA Net3), not a real Maharashtra network.
Wards, leaks, meter noise and all results are SIMULATED.

Run from repo root:  .venv\\Scripts\\python.exe city\\partB_run.py
Outputs: outputs/wards.json, outputs/partB_results.json,
         outputs/partB_compare.png, outputs/partB_leaks.png
"""

import json
import os
import time
from concurrent.futures import ProcessPoolExecutor

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import wntr
from matplotlib.lines import Line2D
from matplotlib.patches import Polygon
from scipy.spatial import ConvexHull

import engine

OUT_DIR = engine.REPO / "outputs"
LABEL = engine.NETWORK_LABEL


# --------------------------------------------------------------------------
# Plot helpers
# --------------------------------------------------------------------------
def draw_wards(ax, wn, wards, fill=None, flagged=(), notes=None):
    """Dashed (padded) convex hull + label per ward; optional fill colour per ward."""
    all_xy = np.array([n.coordinates for _, n in wn.nodes()])
    pad = 0.02 * np.ptp(all_xy, axis=0).max()  # keeps thin/collinear wards visible
    ring = pad * np.array([[np.cos(a), np.sin(a)] for a in np.linspace(0, 2 * np.pi, 12, endpoint=False)])
    for w, info in wards["wards"].items():
        nodes = info["demand_junctions"] + info["other_junctions"]
        pts = np.array([wn.get_node(n).coordinates for n in nodes])
        if len(pts):
            padded = (pts[:, None, :] + ring[None, :, :]).reshape(-1, 2)
            hull = padded[ConvexHull(padded).vertices]
            ax.add_patch(Polygon(
                hull, closed=True, zorder=0,
                facecolor=fill[w] if fill else "none", alpha=0.55 if fill else 1,
                edgecolor="black" if w in flagged else "grey",
                linewidth=2.5 if w in flagged else 1, linestyle="-" if w in flagged else "--"))
        cx, cy = pts.mean(axis=0)
        text = w + (f"\n{notes[w]}" if notes else "")
        ax.text(cx, cy, text, ha="center", va="center", fontsize=9, fontweight="bold", zorder=5,
                bbox=dict(boxstyle="round,pad=0.2", fc="white", ec="none", alpha=0.8))


def plot_compare(wn, wards, scenarios, cfg, path):
    fig, axes = plt.subplots(1, 2, figsize=(17, 8))
    for ax, (name, sc) in zip(axes, scenarios.items()):
        ratios = pd.Series(sc["junction_ratio"])
        m = sc["metrics"]
        wntr.graphics.plot_network(
            wn, node_attribute=ratios, node_range=[0, 1], node_cmap="RdYlGn", node_size=35,
            link_width=0.6, ax=ax, add_colorbar=True,
            node_colorbar_label="Service ratio (delivered / demanded)",
            title=(f"{name}: {m['reduction_pct']:.1f}% less water | under-served (<0.8): "
                   f"{m['underserved_count']} | dry (<0.5): {m['dry_count']}\n"
                   f"min ratio {m['min_ratio']:.2f} | demand-weighted mean {m['weighted_mean_ratio']:.3f}"))
        dry = [j for j, r in sc["junction_ratio"].items() if r < cfg["dry_threshold"]]
        if dry:
            xy = np.array([wn.get_node(j).coordinates for j in dry])
            ax.scatter(xy[:, 0], xy[:, 1], marker="x", s=120, linewidths=2.5, c="black", zorder=6)
        draw_wards(ax, wn, wards)
        ax.legend(handles=[Line2D([], [], marker="x", ls="", color="black", ms=10, mew=2.5,
                                  label=f"dry junction (<{cfg['dry_threshold']})"),
                           Line2D([], [], ls="--", color="grey", label="simulated ward boundary")],
                  loc="lower left", fontsize=9)
    fig.suptitle(f"BLUNT vs FAIR 10% cut - per-junction service ratio, final 24 h of "
                 f"{cfg['sim_days']}-day PDD run\n{LABEL}", fontsize=12)
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def plot_leaks(wn, wards, leaks, path):
    per = leaks["per_ward"]
    anomalies = {w: v["anomaly_pct"] for w, v in per.items()}
    vmax = 50.0  # cap so moderate anomalies stay visible; colourbar shows >50% as "extend"
    norm = matplotlib.colors.Normalize(vmin=0, vmax=vmax)
    cmap = plt.get_cmap("Reds")
    fill = {w: cmap(norm(max(a, 0))) for w, a in anomalies.items()}
    flagged = [w for w, v in per.items() if v["flagged"]]
    notes = {w: f"{a:+.1f}%" + (" FLAGGED" if w in flagged else "") for w, a in anomalies.items()}

    fig, ax = plt.subplots(figsize=(10, 9))
    wntr.graphics.plot_network(wn, node_size=8, link_width=0.6, ax=ax,
                               title="Night inflow anomaly per ward (02:00-04:00 bulk meters) vs no-leak baseline")
    draw_wards(ax, wn, wards, fill=fill, flagged=flagged, notes=notes)
    xy = np.array([wn.get_node(l["junction"]).coordinates for l in leaks["leaks"]])
    ax.scatter(xy[:, 0], xy[:, 1], marker="*", s=380, c="#1f4fd1", edgecolors="black", zorder=7)
    for l, (x, y) in zip(leaks["leaks"], xy):
        ax.annotate(f"leak @ {l['junction']}", (x, y), xytext=(8, 8), textcoords="offset points", fontsize=8, zorder=7)
    sm = matplotlib.cm.ScalarMappable(norm=norm, cmap=cmap)
    fig.colorbar(sm, ax=ax, shrink=0.6, extend="max", label="Night inflow above baseline (%)")
    ax.legend(handles=[Line2D([], [], marker="*", ls="", ms=16, mfc="#1f4fd1", mec="black", label="injected leak (simulated)"),
                       Line2D([], [], lw=2.5, color="black", label=f"flagged ward ({leaks['threshold'].split(' (')[0]})")],
              loc="lower left", fontsize=9)
    fig.suptitle(LABEL, fontsize=11)
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def main():
    t_total = time.perf_counter()
    cfg = engine._cfg(None)
    OUT_DIR.mkdir(exist_ok=True)
    print(LABEL, "\n")

    # 1. Baseline + timing of ONE scenario run
    base = engine.run_scenario({"return_link_flows": True}, cfg)
    print(f"ONE scenario run (baseline, {cfg['sim_days']}-day WNTRSimulator): {base['runtime_s']:.2f} s wall-clock")
    bvol = base["vol_delivered_m3"]

    # 2. Wards
    wards = engine.assign_wards(cfg, base["link_mean_flow_m3s"])
    (OUT_DIR / "wards.json").write_text(json.dumps(wards, indent=2))
    for w, info in wards["wards"].items():
        print(f"  {w}: {len(info['demand_junctions'])} demand junctions, inlet pipes {info['inlet_pipes']}")
    base_metrics = engine.score(base, bvol, wards, cfg)

    # 3a. BLUNT
    t = time.perf_counter()
    blunt = engine.blunt_scenario(bvol, wards, cfg)
    print(f"\nBLUNT: K={blunt['throttle_settings']} -> {blunt['metrics']['reduction_pct']:.2f}% "
          f"({blunt['n_sims']} sims, {time.perf_counter() - t:.0f} s)")

    # 3b. FAIR (candidates in parallel; each = one future Step Functions job)
    workers = max(1, (os.cpu_count() or 2) - 1)
    t = time.perf_counter()
    with ProcessPoolExecutor(max_workers=workers) as pool:
        fair = engine.fair_search(bvol, wards, cfg, mapper=pool.map)
    fair_wall = time.perf_counter() - t
    cands = fair["candidates"]
    best = fair["best"]
    n_feas = sum(c["feasible"] for c in cands)
    sims = [h for c in cands for h in c["history"] if "runtime_s" in h]
    n_failed = sum("error" in h for c in cands for h in c["history"])
    print(f"FAIR: {len(cands)} candidates ({n_feas} feasible), {len(sims)} sims "
          f"(+{n_failed} non-converged), {fair_wall:.0f} s on {workers} workers")
    print(f"  best = candidate {best['candidate_id']}: {best['metrics']}")

    # 4. Leak detection
    boundaries = {w: info["boundary_links"] for w, info in wards["wards"].items()}
    base_night = engine.run_scenario({"ward_boundaries": boundaries}, cfg)["ward_night_inflow_m3"]
    leaks = engine.leak_detection(base_night, wards, cfg)
    print(f"\nLeaks at {[(l['junction'], l['ward']) for l in leaks['leaks']]}; flagged "
          f"{[w for w, v in leaks['per_ward'].items() if v['flagged']]}")

    # 5. Outputs
    def scenario_out(res, extra=None):
        return {**res["metrics"], "junction_ratio": res["junction_ratio"],
                "throttle_settings": res["throttle_settings"], **(extra or {})}

    candidate_times = [c["runtime_s"] for c in cands]
    results = {
        "label": f"{LABEL} - wards, leaks and results are simulated",
        "config": cfg,
        "timing_s": {
            "one_scenario_run": base["runtime_s"],
            "sim_runtime_mean": round(float(np.mean([h["runtime_s"] for h in sims])), 2),
            "sim_runtime_max": round(float(np.max([h["runtime_s"] for h in sims])), 2),
            "candidate_eval_mean": round(float(np.mean(candidate_times)), 1),
            "candidate_eval_max": round(float(np.max(candidate_times)), 1),
            "fair_search_wall": round(fair_wall, 1),
            "fair_search_workers": workers,
        },
        "baseline": {**base_metrics, "vol_delivered_m3": round(bvol, 1), "water_balance": base["water_balance"]},
        "scenarios": {
            "BLUNT": scenario_out(blunt, {"lever": "same throttle K on both source mains"}),
            "FAIR": scenario_out(best, {"lever": "per-ward throttle K on each ward's inlet pipes",
                                        "ward_settings": best["ward_settings"],
                                        "candidate_id": best["candidate_id"]}),
        },
        "fair_search": {
            "n_candidates": len(cands), "n_feasible": n_feas, "n_simulations": len(sims),
            "n_nonconverged_simulations": n_failed,
            "method": "40 Latin-hypercube per-ward log10 throttle shapes in [-2, 2] + 3 refinement rounds "
                      "(top 3 x 2 Gaussian children, sigma 0.5/0.3/0.15); each candidate scaled to hit the band",
            "ranking": "min under-served count, then max min-ratio, then max demand-weighted mean",
            "candidates": [{"candidate_id": c["candidate_id"], "feasible": c["feasible"],
                            "ward_settings": c.get("ward_settings"), "reason": c.get("reason"),
                            "metrics": {k: v for k, v in c.get("metrics", {}).items() if k != "ward_mean_ratio"},
                            "n_sims": len(c["history"]), "runtime_s": c["runtime_s"]} for c in cands],
        },
        "leaks": leaks,
    }
    results["timing_s"]["total_script"] = round(time.perf_counter() - t_total, 1)
    (OUT_DIR / "partB_results.json").write_text(json.dumps(results, indent=2))

    wn = engine.build_network(config=cfg)
    plot_compare(wn, wards, {"BLUNT": blunt, "FAIR": best}, cfg, OUT_DIR / "partB_compare.png")
    plot_leaks(wn, wards, leaks, OUT_DIR / "partB_leaks.png")

    # Summary
    print("\n================ PART B SUMMARY (SIMULATED) ================")
    rows = []
    for name, sc in results["scenarios"].items():
        rows.append({"scenario": name, "reduction_%": sc["reduction_pct"], "under-served": sc["underserved_count"],
                     "dry": sc["dry_count"], "min_ratio": sc["min_ratio"],
                     "weighted_mean": sc["weighted_mean_ratio"],
                     **{w: r for w, r in sc["ward_mean_ratio"].items()}})
    print(pd.DataFrame(rows).set_index("scenario").T.to_string())
    print(f"\nBLUNT settings: {blunt['throttle_settings']}")
    print(f"FAIR ward settings (K on each ward's inlets): {best['ward_settings']}")
    print("\nLeak detection:")
    for w, v in leaks["per_ward"].items():
        print(f"  {w}: anomaly {v['anomaly_pct']:+6.2f}%  flagged={v['flagged']!s:5}  contains_leak={v['contains_leak']}")
    print(f"  TP {leaks['true_positives']}  FP {leaks['false_positives']}  FN {leaks['false_negatives']}")
    print(f"\nTiming: one scenario run {base['runtime_s']:.2f} s | mean sim {results['timing_s']['sim_runtime_mean']} s | "
          f"mean candidate {results['timing_s']['candidate_eval_mean']} s | total {results['timing_s']['total_script']} s")


if __name__ == "__main__":
    main()
