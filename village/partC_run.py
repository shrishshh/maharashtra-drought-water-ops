"""Part C: Village engine demo - need ranking, tanker routing, fraud checks.

DATA: Tuljapur taluka, Dharashiv - real village locations (OSM) and Census 2011
population; livestock, source status, requests, trips and GPS are SIMULATED.

Run from repo root (after village/data_prep.py):
    .venv\\Scripts\\python.exe -m village.partC_run
Outputs: outputs/partC_results.json, outputs/partC_gps_sim.json,
         outputs/partC_routes.png, outputs/partC_naive_vs_opt.png, outputs/partC_fraud.png,
         outputs/partC_fleet.png
"""

import json
import os
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.lines import Line2D

from village import engine

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "data" / "village" / "villages.json"
OUT = REPO / "outputs"
LABEL = ("Tuljapur taluka, Dharashiv - real village locations (OSM) + Census 2011 population; "
         "livestock / source status / requests / trips / GPS SIMULATED")
N_FRAUD_SEEDS = 20


def add_basemap(ax):
    try:
        import contextily as ctx
        ctx.add_basemap(ax, crs="EPSG:4326", source=ctx.providers.Esri.WorldStreetMap, alpha=0.6,
                        attribution="Basemap: Esri World Street Map (Esri, HERE, Garmin, (c) OpenStreetMap contributors)", zoom=11)
        return True
    except Exception as exc:  # offline / tile server down -> plain map
        print(f"  basemap skipped: {exc}")
        return False


def plot_routes(data, ranked, plan, path):
    vill = {v["id"]: v for v in data["villages"]}
    score = {r["id"]: r["need_score"] for r in ranked}
    eligible = {r["id"] for r in ranked if r["eligible"]}
    fps = {f["name"]: f for f in data["fill_points"]}
    used_fps = [fps[n] for n in plan.get("fill_points", [data["fill_points"][0]["name"]])]
    base = used_fps[0]
    served = plan["litres_by_village"]
    fig, ax = plt.subplots(figsize=(11, 10))
    colors = plt.get_cmap("tab10").colors
    for k, r in enumerate(plan["routes"]):
        pts = [(base["lon"], base["lat"])] + [
            ((vill[e["village_id"]]["lon"], vill[e["village_id"]]["lat"]) if e["type"] == "deliver"
             else (fps[e["place"]]["lon"], fps[e["place"]]["lat"])) for e in r["events"]] + [(base["lon"], base["lat"])]
        jitter = (k - len(plan["routes"]) / 2) * 0.0005  # separate overlapping spokes
        xs, ys = zip(*[(x + jitter, y + jitter) for x, y in pts])
        ax.plot(xs, ys, "-", color=colors[k % 10], lw=1.8, alpha=0.8, zorder=2,
                label=f"{r['tanker']}: {r['trips']} trips, {r['km']:.0f} km, {r['litres'] / 1000:.0f} kL")
    elig_v = [v for v in data["villages"] if v["id"] in eligible]
    other_v = [v for v in data["villages"] if v["id"] not in eligible]
    ax.scatter([v["lon"] for v in other_v], [v["lat"] for v in other_v], s=[20 + v["population"] / 25 for v in other_v],
               facecolors="none", edgecolors="grey", linewidths=1, zorder=3)
    sc = ax.scatter([v["lon"] for v in elig_v], [v["lat"] for v in elig_v], s=[20 + v["population"] / 25 for v in elig_v],
                    c=[score[v["id"]] / 1000 for v in elig_v], cmap="YlOrRd", edgecolors="black", linewidths=0.6, zorder=3)
    dropped = [v for v in elig_v if served.get(v["id"], 0) == 0]
    ax.scatter([v["lon"] for v in dropped], [v["lat"] for v in dropped], marker="x", s=70, c="black",
               linewidths=2, zorder=4)
    high = {r["id"] for r in ranked if r["high_need"]}
    for v in data["villages"]:
        if v["id"] in high:
            ax.annotate(v["name"], (v["lon"], v["lat"]), xytext=(6, 5), textcoords="offset points", fontsize=8,
                        fontweight="bold", zorder=5)
    for i, fp in enumerate(used_fps):
        ax.scatter([fp["lon"]], [fp["lat"]], marker="*", s=500, c="#1f4fd1", edgecolors="white", zorder=6)
        ax.annotate(f"{'Base + filling point' if i == 0 else 'Filling point'}: {fp['name']} (assumed)",
                    (fp["lon"], fp["lat"]), xytext=(12, 10) if i == 0 else (-60, -22), textcoords="offset points", fontsize=9,
                    color="#1f4fd1", fontweight="bold", zorder=6)
    add_basemap(ax)
    fig.colorbar(sc, ax=ax, shrink=0.6, label="Need score (thousand litre-units; size = Census 2011 population)")
    handles, labels = ax.get_legend_handles_labels()
    handles += [Line2D([], [], marker="x", ls="", color="black", mew=2, label="eligible, no water today (dropped)"),
                Line2D([], [], marker="o", ls="", mfc="none", mec="grey", label="not eligible (source not dry, SIMULATED)"),
                Line2D([], [], marker="*", ls="", ms=15, color="#1f4fd1", label="filling point")]
    ax.legend(handles=handles, loc="lower left", bbox_to_anchor=(0, 0.035), fontsize=8, framealpha=0.9)
    ax.set_xlabel("longitude")
    ax.set_ylabel("latitude")
    ax.set_title(f"{plan['plan']}: one-day plan for {len(elig_v)} tanker-eligible villages; "
                 f"bold = top-25% need", fontsize=11)
    fig.suptitle(LABEL, fontsize=10)
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def plot_compare(rows, path, n_tankers):
    n_high = rows[0]["high_need_villages"]
    metrics = [("total_km", "Total km driven", "{:.0f}"), ("tanker_hours", "Tanker-hours", "{:.1f}"),
               ("litres_delivered", "Litres delivered (kL)", "{:.0f}"), ("villages_served", "Villages served", "{:.0f}"),
               ("high_need_unserved", f"High-need villages\nleft unserved (of {n_high})", "{:.0f}")]
    fig, axes = plt.subplots(1, len(metrics), figsize=(19, 4.4))
    colors = ["#9aa5b1", "#1f6feb", "#0b8a5f"]
    names = ["Naive\nFCFS", "OR-Tools\n1 fill pt", "OR-Tools\n2 fill pts"][:len(rows)]
    for ax, (key, title, fmt) in zip(axes, metrics):
        vals = [r[key] / (1000 if key == "litres_delivered" else 1) for r in rows]
        bars = ax.bar(names, vals, color=colors[:len(rows)], width=0.6)
        for b, v in zip(bars, vals):
            ax.text(b.get_x() + b.get_width() / 2, b.get_height(), fmt.format(v), ha="center", va="bottom", fontsize=10)
        ax.set_title(title, fontsize=10)
        ax.spines[["top", "right"]].set_visible(False)
        ax.set_ylim(0, max(vals) * 1.18 if max(vals) else 1)
    fig.suptitle(f"Tanker plans for eligible (source-dry) villages, {n_tankers} tankers, one day\n{LABEL}", fontsize=10)
    fig.tight_layout()
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def plot_fleet(fleet, path):
    rows = fleet["sweep"]  # includes the small probe sizes used to locate threshold (a)
    n = [r["n_tankers"] for r in rows]
    fig, ax = plt.subplots(figsize=(11, 6))
    ax.plot(n, [r["need_covered_pct"] for r in rows], "o-", color="#1f6feb", label="full need covered (human + livestock)")
    ax.plot(n, [r["human_need_covered_pct"] for r in rows], "s-", color="#0b8a5f",
            label="human drinking need covered (20 lpcd only)")
    for key, label, color in [("min_tankers_every_high_need_one_load", "(a) every high-need village gets >= 1 load", "#c2410c"),
                              ("min_tankers_full_human_need", "(b) 100% of human drinking need", "#6d28d9")]:
        v = fleet[key]
        if v is not None:
            ax.axvline(v, color=color, ls="--", lw=1.5)
            ax.text(v + 0.3, 103, f"{label}: {v} tankers", color=color, fontsize=9, rotation=90, va="top")
    ax.axhline(100, color="grey", lw=0.8)
    ax.set_xlabel("number of tankers (10,000 L, 10 h day, one filling point)")
    ax.set_ylabel("% of daily need delivered (eligible villages)")
    ax.set_ylim(0, 108)
    ax.set_xticks(n)
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(loc="center right", fontsize=9)
    ax.set_title(f"Fleet size vs coverage (each point: OR-Tools, {fleet['solver_time_limit_s_per_point']} s limit, "
                 "so thresholds are upper bounds)", fontsize=10)
    fig.suptitle(LABEL, fontsize=10)
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def fraud_stats(plan, payload, data, cfg, seeds, label):
    evs = []
    for seed in seeds:
        g = engine.simulate_gps_and_claims(plan, payload, cfg, seed=seed)
        rep = engine.detect_fraud({**g, "villages": data["villages"], "fill_points": data["fill_points"]}, cfg)
        evs.append(engine.evaluate_fraud(rep, g["claims"]))
    return {"scenario": label, "seeds": len(seeds), "gps_noise_m": cfg["gps_noise_m"],
            "gps_gap_trip_frac": cfg["gps_gap_trip_frac"], "gps_gap_min": cfg["gps_gap_min"],
            "precision_mean": round(float(np.mean([e["precision"] for e in evs])), 3),
            "precision_min": min(e["precision"] for e in evs),
            "recall_mean": round(float(np.mean([e["recall"] for e in evs])), 3),
            "recall_min": min(e["recall"] for e in evs),
            "false_positives_total": sum(len(e["false_positives"]) for e in evs),
            "missed_total": sum(len(e["missed"]) for e in evs)}


def plot_fraud(data, gps, report, cfg, path):
    vill = {v["id"]: v for v in data["villages"]}
    claims = {c["claim_id"]: c for c in gps["claims"]}
    picks = [("a_no_visit", "(a) delivery claimed, GPS never near the village"),
             ("b_short_stop", "(b) GPS stop far shorter than unload time")]
    fig, axes = plt.subplots(1, 2, figsize=(16, 7.5))
    for ax, (ftype, title) in zip(axes, picks):
        c = next(c for c in gps["claims"] if c["injected_fraud"] == ftype)
        flag = next(f for f in report["flagged"] if f["claim_id"] == c["claim_id"])
        v = vill[c["village_id"]]
        tr = np.array([p for p in gps["traces"][c["tanker"]]
                       if abs(p[0] - c["claimed_min"]) <= cfg["claim_window_min"]])
        if ftype == "b_short_stop":  # zoom to ~3 km around the village so the brief stop is visible
            zoom = 0.027
            tr = tr[(np.abs(tr[:, 1] - v["lat"]) < zoom) & (np.abs(tr[:, 2] - v["lon"]) < zoom)]
            ax.set_xlim(v["lon"] - zoom, v["lon"] + zoom)
            ax.set_ylim(v["lat"] - zoom, v["lat"] + zoom)
        sc = ax.scatter(tr[:, 2], tr[:, 1], c=tr[:, 0] - c["claimed_min"], cmap="coolwarm", s=14, zorder=3)
        ax.plot(tr[:, 2], tr[:, 1], "-", color="grey", lw=0.6, zorder=2)
        r_deg = cfg["visit_radius_m"] / 111_320
        ax.add_patch(plt.Circle((v["lon"], v["lat"]), r_deg / np.cos(np.radians(v["lat"])) * 1, fill=False,
                                ec="black", lw=1.5, ls="--", zorder=4))
        ax.scatter([v["lon"]], [v["lat"]], marker="P", s=220, c="red", edgecolors="black", zorder=5)
        ax.annotate(f"claimed: {v['name']}\n{c['litres']:,} L at {flag['claimed_time']}", (v["lon"], v["lat"]),
                    xytext=(10, 10), textcoords="offset points", fontsize=9, color="darkred", zorder=6)
        ax.set_aspect(1 / np.cos(np.radians(v["lat"])), adjustable="box")
        ax.set_title(f"{title}\n{c['claim_id']} by {c['tanker']}: " + "; ".join(flag["reasons"]), fontsize=9, wrap=True)
        ax.set_xlabel("longitude")
        ax.set_ylabel("latitude")
        fig.colorbar(sc, ax=ax, shrink=0.6, label="minutes relative to claimed time")
        ax.legend(handles=[Line2D([], [], marker="o", ls="", color="grey",
                                  label=f"GPS point (1/min, {cfg['gps_noise_m']} m noise, SIMULATED)"),
                           Line2D([], [], ls="--", color="black", label=f"{cfg['visit_radius_m']} m visit radius"),
                           Line2D([], [], marker="P", ls="", ms=12, mfc="red", mec="black", label="claimed delivery")],
                  loc="best", fontsize=8, framealpha=0.9)
    fig.suptitle(f"Fraud check examples - GPS trace vs claimed stop (+/- {cfg['claim_window_min']} min)\n"
                 f"{LABEL}", fontsize=10)
    fig.tight_layout()
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def main():
    t_total = time.perf_counter()
    cfg = engine._cfg(None)
    data = json.loads(DATA.read_text(encoding="utf-8"))
    print(LABEL, "\n")

    need = engine.need_assessment(data, cfg)
    ranked = need["ranked"]
    print(f"Eligible (source dry, assumption): {need['n_eligible']} of {len(ranked)} villages; "
          f"need {need['total_need_l']:,} L/day = {need['total_loads_needed']} tanker loads")
    payload = {**data, "ranked": ranked}

    naive = engine.naive_plan(payload, cfg)
    opt = engine.plan_routes(payload, cfg)
    opt2 = engine.plan_routes(payload, {**cfg, "n_fill_points": 2})
    print(f"OR-Tools solver: {opt['runtime_s']:.1f} s (limit {cfg['solver_time_limit_s']} s); "
          f"2 filling points: {opt2['runtime_s']:.1f} s; naive: {naive['runtime_s']:.3f} s")
    table = engine.compare_plans([naive, opt, opt2], ranked)

    # Fleet sweep (each point is an independent job -> process pool now, Step Functions Map later)
    workers = max(1, (os.cpu_count() or 2) - 1)
    with ProcessPoolExecutor(max_workers=workers) as pool:
        fleet = engine.plan_fleet(data, cfg, mapper=pool.map)
    print(f"Fleet sweep: {len(fleet['sweep']) * 2} solves x {fleet['solver_time_limit_s_per_point']} s on "
          f"{workers} workers = {fleet['runtime_s']} s")

    # Fraud: headline seed for the report/plot + robustness over many seeds + a harsher stress test
    gps = engine.simulate_gps_and_claims(opt, payload, cfg)
    t = time.perf_counter()
    report = engine.detect_fraud({**gps, "villages": data["villages"], "fill_points": data["fill_points"]}, cfg)
    detect_s = time.perf_counter() - t
    evaluation = engine.evaluate_fraud(report, gps["claims"])
    seeds = range(N_FRAUD_SEEDS)
    robustness = [
        fraud_stats(opt, payload, data, cfg, seeds, "realistic: 30 m noise, 2-5 min gaps on ~10% of trips"),
        fraud_stats(opt, payload, data, {**cfg, "gps_noise_m": 50, "gps_gap_trip_frac": 0.3, "gps_gap_min": [10, 20]},
                    seeds, "stress: 50 m noise, 10-20 min gaps on ~30% of trips"),
    ]

    (OUT / "partC_gps_sim.json").write_text(json.dumps(gps), encoding="utf-8")
    results = {
        "label": LABEL,
        "data_sources": data["sources"],
        "simulated_fields": data["simulated_fields"],
        "census_match": data["census_match"],
        "config": cfg,
        "need": {k: v for k, v in need.items()},
        "comparison": table,
        "plans": {"naive": naive, "optimised": opt, "optimised_2_fill_points": opt2},
        "fleet": fleet,
        "fraud": {"report": report, "evaluation": evaluation, "robustness": robustness,
                  "gaps": gps["gaps"], "claims": gps["claims"]},
        "timings_s": {"solver": opt["runtime_s"], "solver_2_fill_points": opt2["runtime_s"], "naive": naive["runtime_s"],
                      "fleet_sweep": fleet["runtime_s"], "fraud_detection": round(detect_s, 3)},
    }

    plot_routes(data, ranked, opt, OUT / "partC_routes.png")
    plot_routes(data, ranked, opt2, OUT / "partC_routes_2fill.png")
    plot_compare(table, OUT / "partC_naive_vs_opt.png", cfg["n_tankers"])
    plot_fleet(fleet, OUT / "partC_fleet.png")
    plot_fraud(data, gps, report, cfg, OUT / "partC_fraud.png")
    results["timings_s"]["total_script"] = round(time.perf_counter() - t_total, 1)
    (OUT / "partC_results.json").write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")

    print("\n================ PART C.1 SUMMARY (livestock/source status/trips SIMULATED) ================")
    keys = ["total_km", "tanker_hours", "litres_delivered", "trips", "villages_served", "high_need_villages",
            "high_need_unserved", "litres_to_high_need", "need_covered_pct", "litres_per_km"]
    print(f"{'metric':<22}{'naive':>12}{'opt 1 fill':>12}{'opt 2 fill':>12}")
    for k in keys:
        print(f"{k:<22}" + "".join(f"{row[k]:>12,}" for row in table))
    print("\nFleet sweep (full need %, high-need unserved, villages served, human need %):")
    for r in fleet["sweep"]:
        print(f"  {r['n_tankers']:>3} tankers: {r['need_covered_pct']:5.1f}%  {r['high_need_unserved']}  "
              f"{r['villages_served']:>2}  {r['human_need_covered_pct']:5.1f}%")
    print(f"  (a) min tankers, every high-need village >= 1 load: {fleet['min_tankers_every_high_need_one_load']}")
    print(f"  (b) min tankers, 100% human drinking need: {fleet['min_tankers_full_human_need']}")
    print(f"\nFraud (seed 99): {evaluation['flagged']} flagged / {evaluation['injected']} injected / "
          f"{report['n_claims']} claims | precision {evaluation['precision']} recall {evaluation['recall']} | "
          f"{len(gps['gaps'])} GPS gaps")
    for f in report["flagged"]:
        print(f"  {f['claim_id']} {f['tanker']} {f['village']} {f['claimed_time']}: {' | '.join(f['reasons'])}")
    for r in robustness:
        print(f"  {r['scenario']}: precision mean {r['precision_mean']} (min {r['precision_min']}), "
              f"recall mean {r['recall_mean']} (min {r['recall_min']}), FP {r['false_positives_total']}, "
              f"missed {r['missed_total']} over {r['seeds']} seeds")
    print(f"\nTotal runtime {results['timings_s']['total_script']} s")


if __name__ == "__main__":
    main()
