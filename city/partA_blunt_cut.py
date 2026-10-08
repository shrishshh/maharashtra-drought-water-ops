"""Part A proof of concept: what a "blunt" 10% water cut does to service equity.

NETWORK: representative sample network (EPA Net3), not a real Maharashtra network.
All results from this script are SIMULATED on that sample network.

Hydraulics live in engine.py (see its docstring for modelling notes). This
script:
  1. Runs the baseline (PDD, 7-day WNTRSimulator run, last 24 h scored).
  2. Checks whether slowing all pumps (lever a) can reach a 10% cut; on Net3
     it cannot (River reservoir feeds by gravity), so
  3. falls back to the same throttle on both source mains (lever b, BLUNT).
  4. Saves outputs/partA_summary.csv and outputs/partA_maps.png.

Run from repo root:  .venv\\Scripts\\python.exe city\\partA_blunt_cut.py
"""

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
import wntr

import engine

OUT_DIR = engine.REPO / "outputs"
NETWORK_LABEL = "Representative sample network (EPA Net3), not a real Maharashtra network - SIMULATED"
CONFIG = {"reduction_band": [0.0975, 0.1025]}  # Part A used a tighter band than Part B
MIN_SPEED_FACTOR = 0.05  # lowest pump speed tried for lever (a)


def main() -> None:
    cfg = engine._cfg(CONFIG)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print(NETWORK_LABEL)
    print(f"PDD: required {cfg['required_pressure_m']} m, minimum {cfg['minimum_pressure_m']} m | "
          f"{cfg['sim_days']}-day run, last {cfg['eval_hours']} h scored, WNTRSimulator\n")

    base = engine.run_scenario({}, cfg)
    bvol = base["vol_delivered_m3"]
    print(f"Baseline: delivered {bvol:,.0f} of {base['vol_expected_m3']:,.0f} m3 demanded "
          f"({bvol / base['vol_expected_m3'] * 100:.1f}%)  [{base['runtime_s']:.2f} s]")

    print(f"\nLever (a) pump speed: testing slowest allowed speed ({MIN_SPEED_FACTOR})...")
    slow = engine.run_scenario({"speed_factor": MIN_SPEED_FACTOR}, cfg)
    max_pump_red = 1 - slow["vol_delivered_m3"] / bvol
    print(f"  max reduction achievable by pump speed alone: {max_pump_red * 100:.2f}%")
    if max_pump_red >= cfg["target_reduction"]:
        raise SystemExit("Pump speed can reach the target on this network - add a speed search before using lever (b).")
    print(f"  -> cannot deliver a {cfg['target_reduction']:.0%} cut; using lever (b): same throttle on "
          f"source mains {cfg['source_mains']}")

    cut = engine.blunt_scenario(bvol, config=cfg)
    k = next(iter(cut["throttle_settings"].values()))
    m = cut["metrics"]
    lever_desc = f"source mains throttled, valve K = {k:.0f}"

    base_m = engine.score(base, bvol, config=cfg)
    summary = pd.DataFrame({"baseline_ratio": base["junction_ratio"], "cut_ratio": cut["junction_ratio"]})
    summary.index.name = "junction"
    summary.to_csv(OUT_DIR / "partA_summary.csv")

    wn = engine.build_network(config=cfg)
    fig, axes = plt.subplots(1, 2, figsize=(16, 7.5))
    for ax, col, title in [
        (axes[0], "baseline_ratio", "Baseline (no cut)"),
        (axes[1], "cut_ratio", f"Blunt cut: {lever_desc}\n({m['reduction_pct']:.1f}% less water delivered)"),
    ]:
        wntr.graphics.plot_network(
            wn, node_attribute=summary[col], node_range=[0, 1], node_cmap="RdYlGn",
            node_size=30, link_width=0.6, title=title, ax=ax,
            add_colorbar=True, node_colorbar_label="Service ratio (delivered / demanded)",
        )
    fig.suptitle(f"Per-junction service ratio, final 24 h of {cfg['sim_days']}-day PDD run\n{NETWORK_LABEL}", fontsize=11)
    fig.tight_layout()
    fig.savefig(OUT_DIR / "partA_maps.png", dpi=150, bbox_inches="tight")
    plt.close(fig)

    print("\n================ PART A SUMMARY (SIMULATED) ================")
    print(f"Junctions with demand:          {len(summary)}")
    print(f"Pump-speed-only max reduction:  {max_pump_red * 100:.2f}% (at speed x{MIN_SPEED_FACTOR})")
    print(f"Blunt cut used:                 {lever_desc} ({cut['n_sims']} sims)")
    print(f"Delivered volume (24 h):        {bvol:,.0f} -> {bvol * (1 - m['reduction_pct'] / 100):,.0f} m3")
    print(f"Volume reduction vs baseline:   {m['reduction_pct']:.2f}%")
    print(f"Effectively dry (<{cfg['dry_threshold']}):        baseline {base_m['dry_count']:3d}  ->  cut {m['dry_count']:3d}")
    print(f"Under-served (<{cfg['underserved_threshold']}):          baseline {base_m['underserved_count']:3d}  ->  cut {m['underserved_count']:3d}")
    print(f"Min service ratio:              {base_m['min_ratio']:.3f} -> {m['min_ratio']:.3f}")
    print(f"Saved: {OUT_DIR / 'partA_summary.csv'}")
    print(f"Saved: {OUT_DIR / 'partA_maps.png'}")


if __name__ == "__main__":
    main()
