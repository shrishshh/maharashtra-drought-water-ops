"""Part C: Village engine demo - need ranking, tanker routing, fraud checks.

DATA: Tuljapur taluka, Dharashiv - real village locations (OSM) and Census 2011
population; livestock, source status, requests, trips and GPS are SIMULATED.

Run from repo root (after village/data_prep.py):
    .venv\\Scripts\\python.exe village\\partC_run.py
Outputs: outputs/partC_results.json, outputs/partC_gps_sim.json,
         outputs/partC_routes.png, outputs/partC_naive_vs_opt.png, outputs/partC_fraud.png
"""

import json
import time
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.lines import Line2D

import engine

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "data" / "village" / "villages.json"
OUT = REPO / "outputs"
LABEL = ("Tuljapur taluka, Dharashiv - real village locations (OSM) + Census 2011 population; "
         "livestock / requests / trips / GPS SIMULATED")


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
    fp = data["fill_point"]
    served = plan["litres_by_village"]
    fig, ax = plt.subplots(figsize=(11, 10))
    colors = plt.get_cmap("tab10").colors
    for k, r in enumerate(plan["routes"]):
        pts = [(fp["lon"], fp["lat"])] + [
            ((vill[e["village_id"]]["lon"], vill[e["village_id"]]["lat"]) if e["type"] == "deliver" else (fp["lon"], fp["lat"]))
            for e in r["events"]] + [(fp["lon"], fp["lat"])]
        jitter = (k - len(plan["routes"]) / 2) * 0.0005  # separate overlapping spokes
        xs, ys = zip(*[(x + jitter, y + jitter) for x, y in pts])
        ax.plot(xs, ys, "-", color=colors[k % 10], lw=1.8, alpha=0.8, zorder=2,
                label=f"{r['tanker']}: {r['trips']} trips, {r['km']:.0f} km, {r['litres'] / 1000:.0f} kL")
    xs = [v["lon"] for v in data["villages"]]
    ys = [v["lat"] for v in data["villages"]]
    sizes = [20 + v["population"] / 25 for v in data["villages"]]
    sc = ax.scatter(xs, ys, s=sizes, c=[score[v["id"]] / 1000 for v in data["villages"]], cmap="YlOrRd",
                    edgecolors="black", linewidths=0.6, zorder=3)
    dropped = [v for v in data["villages"] if served.get(v["id"], 0) == 0]
    ax.scatter([v["lon"] for v in dropped], [v["lat"] for v in dropped], marker="x", s=70, c="black",
               linewidths=2, zorder=4)
    high = {r["id"] for r in ranked if r["high_need"]}
    for v in data["villages"]:
        if v["id"] in high:
            ax.annotate(v["name"], (v["lon"], v["lat"]), xytext=(6, 5), textcoords="offset points", fontsize=8,
                        fontweight="bold", zorder=5)
    ax.scatter([fp["lon"]], [fp["lat"]], marker="*", s=500, c="#1f4fd1", edgecolors="white", zorder=6)
    ax.annotate(f"Filling point: {fp['name']} (assumed)", (fp["lon"], fp["lat"]), xytext=(12, 10),
                textcoords="offset points", fontsize=9, color="#1f4fd1", fontweight="bold", zorder=6)
    add_basemap(ax)
    fig.colorbar(sc, ax=ax, shrink=0.6, label="Need score (thousand litre-units; size = Census 2011 population)")
    handles, labels = ax.get_legend_handles_labels()
    handles += [Line2D([], [], marker="x", ls="", color="black", mew=2, label="no water today (dropped)"),
                Line2D([], [], marker="*", ls="", ms=15, color="#1f4fd1", label="filling point")]
    ax.legend(handles=handles, loc="upper right", fontsize=8, framealpha=0.9)
    ax.set_xlabel("longitude")
    ax.set_ylabel("latitude")
    ax.set_title("Optimised one-day tanker plan (OR-Tools); bold names = top-25% need", fontsize=12)
    fig.suptitle(LABEL, fontsize=10)
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def plot_compare(rows, path):
    metrics = [("total_km", "Total km driven", "{:.0f}"), ("tanker_hours", "Tanker-hours", "{:.1f}"),
               ("litres_delivered", "Litres delivered (kL)", "{:.0f}"), ("villages_served", "Villages served", "{:.0f}"),
               ("high_need_unserved", "High-need villages\nleft unserved (of 8)", "{:.0f}")]
    fig, axes = plt.subplots(1, len(metrics), figsize=(17, 4.2))
    colors = ["#9aa5b1", "#1f6feb"]
    for ax, (key, title, fmt) in zip(axes, metrics):
        vals = [r[key] / (1000 if key == "litres_delivered" else 1) for r in rows]
        bars = ax.bar(["Naive\nFCFS", "Optimised\nOR-Tools"], vals, color=colors, width=0.6)
        for b, v in zip(bars, vals):
            ax.text(b.get_x() + b.get_width() / 2, b.get_height(), fmt.format(v), ha="center", va="bottom", fontsize=10)
        ax.set_title(title, fontsize=10)
        ax.spines[["top", "right"]].set_visible(False)
        ax.set_ylim(0, max(vals) * 1.18 if max(vals) else 1)
    fig.suptitle(f"Naive first-come-first-served vs optimised tanker plan (6 tankers, one day)\n{LABEL}", fontsize=10)
    fig.tight_layout()
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)


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
        ax.legend(handles=[Line2D([], [], marker="o", ls="", color="grey", label="GPS point (1/min, SIMULATED)"),
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
    print(f"Need: {need['total_need_l']:,} L/day = {need['total_loads_needed']} tanker loads for {len(ranked)} villages")
    payload = {**data, "ranked": ranked}

    naive = engine.naive_plan(payload, cfg)
    opt = engine.plan_routes(payload, cfg)
    print(f"OR-Tools solver: {opt['runtime_s']:.1f} s (time limit {cfg['solver_time_limit_s']} s); "
          f"naive: {naive['runtime_s']:.3f} s")
    table = engine.compare_plans([naive, opt], ranked)

    gps = engine.simulate_gps_and_claims(opt, payload, cfg)
    t = time.perf_counter()
    report = engine.detect_fraud({**gps, "villages": data["villages"], "fill_point": data["fill_point"]}, cfg)
    detect_s = time.perf_counter() - t
    evaluation = engine.evaluate_fraud(report, gps["claims"])

    (OUT / "partC_gps_sim.json").write_text(json.dumps(gps), encoding="utf-8")
    results = {
        "label": LABEL,
        "data_sources": data["sources"],
        "simulated_fields": data["simulated_fields"],
        "census_match": data["census_match"],
        "config": cfg,
        "need": {"total_need_l": need["total_need_l"], "total_loads_needed": need["total_loads_needed"],
                 "ranked": ranked},
        "comparison": table,
        "plans": {"naive": naive, "optimised": opt},
        "fraud": {"report": report, "evaluation": evaluation,
                  "claims": [{k: v for k, v in c.items()} for c in gps["claims"]]},
        "timings_s": {"solver": opt["runtime_s"], "naive": naive["runtime_s"], "fraud_detection": round(detect_s, 3)},
    }

    plot_routes(data, ranked, opt, OUT / "partC_routes.png")
    plot_compare(table, OUT / "partC_naive_vs_opt.png")
    plot_fraud(data, gps, report, cfg, OUT / "partC_fraud.png")
    results["timings_s"]["total_script"] = round(time.perf_counter() - t_total, 1)
    (OUT / "partC_results.json").write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")

    print("\n================ PART C SUMMARY (livestock/trips SIMULATED) ================")
    print("Top 8 by need score:")
    for r in ranked[:8]:
        print(f"  {r['rank']:2d}. {r['name']:<14} pop {r['population']:>5}  need {r['daily_need_l']:>7,} L  "
              f"x{r['urgency']:.2f}  score {r['need_score']:>9,.0f}  loads {r['loads_needed']}")
    keys = ["total_km", "tanker_hours", "litres_delivered", "trips", "villages_served", "high_need_unserved",
            "litres_to_high_need", "need_covered_pct", "litres_per_km"]
    print(f"\n{'metric':<22}{'naive':>14}{'optimised':>14}")
    for k in keys:
        print(f"{k:<22}{table[0][k]:>14,}{table[1][k]:>14,}")
    print(f"\nFraud: {evaluation['flagged']} flagged / {evaluation['injected']} injected / {report['n_claims']} claims | "
          f"precision {evaluation['precision']} recall {evaluation['recall']} | types caught {evaluation['types_caught']}")
    for f in report["flagged"]:
        print(f"  {f['claim_id']} {f['tanker']} {f['village']} {f['claimed_time']}: {' | '.join(f['reasons'])}")
    print(f"\nTotal runtime {results['timings_s']['total_script']} s")


if __name__ == "__main__":
    main()
