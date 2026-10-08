"""Part A proof of concept: what a "blunt" 10% water cut does to service equity.

NETWORK: representative sample network (EPA Net3), not a real Maharashtra network.
All results from this script are SIMULATED on that sample network.

Steps:
  1. Baseline pressure-driven-demand (PDD) simulation.
  2. "Blunt cut" = the same reduction applied to every source, sized by binary
     search so total delivered volume drops ~10% vs baseline.
       a. First lever tried: all pumps slowed by the same speed factor.
       b. If (a) cannot reach the target (on Net3 it cannot - see NOTES), fall
          back to throttling every source main with the same valve loss
          coefficient K (like partially closing the outlet valve at each
          source by the same amount).
  3. Per-junction service ratio (delivered / demanded over 24 h), counts of
     dry / under-served junctions, CSV + side-by-side network maps.

NOTES (findings while building this, see outputs printed by the script):
  - Over a single 24 h run, Net3's three tanks absorb any supply cut, so we run
    SIM_DAYS and evaluate only the LAST 24 h (a sustained cut, tanks drawn down).
  - We use WNTRSimulator, not EpanetSimulator: with the EPANET engine, empty
    tanks (at min level) kept supplying ~690 L/s for days with no inflow,
    violating mass balance, so delivery never dropped. WNTRSimulator conserves
    mass (junction delivery == reservoir + tank supply, checked).
  - WNTRSimulator does not support pump speed != 1.0, so pump speed is applied
    via the affinity laws on each pump curve (Q -> s*Q, H -> s^2*H).
  - Net3's River reservoir (HGL ~67 m) can feed the network by gravity, so
    slowing pumps (even to 5%) barely reduces delivery under PDD.

Run from repo root:  .venv\\Scripts\\python.exe city\\partA_blunt_cut.py
"""

import math
import warnings
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
import wntr

warnings.filterwarnings("ignore", category=FutureWarning)

# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------
REPO = Path(__file__).resolve().parents[1]
INP_FILE = REPO / "data" / "networks" / "Net3.inp"  # EPA Net3 sample, NOT a real Maharashtra network
OUT_DIR = REPO / "outputs"
NETWORK_LABEL = "Representative sample network (EPA Net3), not a real Maharashtra network - SIMULATED"

SIM_DAYS = 7  # simulate a week ...
EVAL_HOURS = 24  # ... and score only the final 24 h
DURATION_H = SIM_DAYS * 24
HYD_TIMESTEP_S = 3600

# PDD pressures (metres). 12 m is the CPHEEO Manual on Water Supply (1999)
# minimum residual pressure for two-storey buildings (7 m single, 17 m
# three-storey). Believed correct but should be checked against the latest
# CPHEEO/MJP guidance before relying on it.
REQUIRED_PRESSURE_M = 12.0
MINIMUM_PRESSURE_M = 0.0
PRESSURE_EXPONENT = 0.5

TARGET_REDUCTION = 0.10  # Maharashtra mandatory 10% cut
REDUCTION_TOL = 0.0025  # accept 9.75% - 10.25%
DRY_THRESHOLD = 0.5  # service ratio below this = "effectively dry"
UNDERSERVED_THRESHOLD = 0.8  # below this = "under-served"

MIN_SPEED_FACTOR = 0.05  # lowest pump speed tried for lever (a)
SOURCE_MAINS = ["60", "101"]  # Net3: River main, Lake main (all supply passes through these)
K_RANGE = (1.0, 1e5)  # valve loss coefficient search range for lever (b)


# --------------------------------------------------------------------------
# Simulation helpers
# --------------------------------------------------------------------------
def build_network(speed_factor: float = 1.0, throttle_k: float = 0.0) -> wntr.network.WaterNetworkModel:
    wn = wntr.network.WaterNetworkModel(str(INP_FILE))
    wn.options.time.duration = DURATION_H * 3600
    wn.options.time.hydraulic_timestep = HYD_TIMESTEP_S
    wn.options.time.report_timestep = HYD_TIMESTEP_S
    wn.options.time.pattern_timestep = HYD_TIMESTEP_S
    wn.options.quality.parameter = "NONE"  # hydraulics only

    wn.options.hydraulic.demand_model = "PDD"
    wn.options.hydraulic.required_pressure = REQUIRED_PRESSURE_M
    wn.options.hydraulic.minimum_pressure = MINIMUM_PRESSURE_M
    wn.options.hydraulic.pressure_exponent = PRESSURE_EXPONENT

    # Lever (a): pump speed via affinity laws on each head curve.
    if speed_factor != 1.0:
        for _, pump in wn.pumps():
            curve = wn.get_curve(pump.pump_curve_name)
            curve.points = [(q * speed_factor, h * speed_factor**2) for q, h in curve.points]

    # Lever (b): same throttling valve loss on every source main.
    if throttle_k:
        for name in SOURCE_MAINS:
            wn.get_link(name).minor_loss = throttle_k
    return wn


def run(speed_factor: float = 1.0, throttle_k: float = 0.0) -> dict:
    """Simulate; return per-junction service ratio and volumes (m^3) for the eval window."""
    wn = build_network(speed_factor, throttle_k)
    results = wntr.sim.WNTRSimulator(wn).run_sim()

    junctions = wn.junction_name_list
    # Eval window = last EVAL_HOURS; rectangle rule over hourly steps
    # (exclude the final endpoint so each hour counts once).
    t0, t1 = (DURATION_H - EVAL_HOURS) * 3600, DURATION_H * 3600
    steps = [t for t in results.node["demand"].index if t0 <= t < t1]

    delivered = results.node["demand"].loc[steps, junctions].clip(lower=0)
    expected = wntr.metrics.expected_demand(wn).loc[steps, junctions]
    vol_delivered = delivered.sum() * HYD_TIMESTEP_S
    vol_expected = expected.sum() * HYD_TIMESTEP_S

    return {
        "ratio": (vol_delivered / vol_expected).where(vol_expected > 0),
        "vol_delivered": float(vol_delivered.sum()),
        "vol_expected": float(vol_expected.sum()),
    }


def bisect(lever: str, lo: float, hi: float, baseline_vol: float, log_scale: bool) -> tuple[float, dict, float]:
    """Binary search lever value between lo (smaller cut) and hi (bigger cut)."""
    best = None
    for _ in range(30):
        mid = math.sqrt(lo * hi) if log_scale else (lo + hi) / 2
        res = run(**{lever: mid})
        red = 1 - res["vol_delivered"] / baseline_vol
        print(f"  {lever} = {mid:10.4f} -> volume reduction {red * 100:6.2f}%")
        best = (mid, res, red)
        if abs(red - TARGET_REDUCTION) <= REDUCTION_TOL:
            break
        if red > TARGET_REDUCTION:
            hi = mid
        else:
            lo = mid
    return best


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print(NETWORK_LABEL)
    print(f"PDD: required {REQUIRED_PRESSURE_M} m, minimum {MINIMUM_PRESSURE_M} m | "
          f"{SIM_DAYS}-day run, last {EVAL_HOURS} h scored, 1 h step, WNTRSimulator\n")

    base = run()
    bvol = base["vol_delivered"]
    print(f"Baseline: delivered {bvol:,.0f} of {base['vol_expected']:,.0f} m3 demanded "
          f"({bvol / base['vol_expected'] * 100:.1f}%)")

    # ---- Lever (a): pump speed ----
    print(f"\nLever (a) pump speed: testing slowest allowed speed ({MIN_SPEED_FACTOR})...")
    slow = run(speed_factor=MIN_SPEED_FACTOR)
    max_pump_red = 1 - slow["vol_delivered"] / bvol
    print(f"  max reduction achievable by pump speed alone: {max_pump_red * 100:.2f}%")

    if max_pump_red >= TARGET_REDUCTION:
        print("Searching pump speed factor...")
        value, cut, reduction = bisect("speed_factor", 1.0, MIN_SPEED_FACTOR, bvol, log_scale=False)
        lever_desc = f"all pumps at {value:.3f} x rated speed"
    else:
        print(f"  -> pump speed cannot deliver a {TARGET_REDUCTION:.0%} cut on this network; "
              f"falling back to lever (b).")
        print(f"\nLever (b) equal throttling valve on source mains {SOURCE_MAINS}: searching K...")
        value, cut, reduction = bisect("throttle_k", *K_RANGE, bvol, log_scale=True)
        lever_desc = f"source mains throttled, valve K = {value:.0f}"

    # ---- outputs ----
    summary = pd.DataFrame({"baseline_ratio": base["ratio"], "cut_ratio": cut["ratio"]})
    summary.index.name = "junction"
    summary.round(4).to_csv(OUT_DIR / "partA_summary.csv")

    served = summary.dropna()  # junctions with non-zero demand

    def counts(col):
        return int((served[col] < DRY_THRESHOLD).sum()), int((served[col] < UNDERSERVED_THRESHOLD).sum())

    b_dry, b_under = counts("baseline_ratio")
    c_dry, c_under = counts("cut_ratio")

    wn = build_network()
    fig, axes = plt.subplots(1, 2, figsize=(16, 7.5))
    for ax, col, title in [
        (axes[0], "baseline_ratio", "Baseline (no cut)"),
        (axes[1], "cut_ratio", f"Blunt cut: {lever_desc}\n({reduction * 100:.1f}% less water delivered)"),
    ]:
        wntr.graphics.plot_network(
            wn, node_attribute=served[col], node_range=[0, 1], node_cmap="RdYlGn",
            node_size=30, link_width=0.6, title=title, ax=ax,
            add_colorbar=True, node_colorbar_label="Service ratio (delivered / demanded)",
        )
    fig.suptitle(f"Per-junction service ratio, final 24 h of {SIM_DAYS}-day PDD run\n{NETWORK_LABEL}", fontsize=11)
    fig.tight_layout()
    fig.savefig(OUT_DIR / "partA_maps.png", dpi=150, bbox_inches="tight")

    print("\n================ PART A SUMMARY (SIMULATED) ================")
    print(f"Junctions with demand:          {len(served)} (of {len(summary)})")
    print(f"Pump-speed-only max reduction:  {max_pump_red * 100:.2f}% (at speed x{MIN_SPEED_FACTOR})")
    print(f"Blunt cut used:                 {lever_desc}")
    print(f"Delivered volume (24 h):        {bvol:,.0f} -> {cut['vol_delivered']:,.0f} m3")
    print(f"Volume reduction vs baseline:   {reduction * 100:.2f}%")
    print(f"Effectively dry (<{DRY_THRESHOLD}):        baseline {b_dry:3d}  ->  cut {c_dry:3d}")
    print(f"Under-served (<{UNDERSERVED_THRESHOLD}):          baseline {b_under:3d}  ->  cut {c_under:3d}")
    print(f"Mean service ratio:             {served['baseline_ratio'].mean():.3f} -> {served['cut_ratio'].mean():.3f}")
    print(f"Min service ratio:              {served['baseline_ratio'].min():.3f} -> {served['cut_ratio'].min():.3f}")
    print(f"Saved: {OUT_DIR / 'partA_summary.csv'}")
    print(f"Saved: {OUT_DIR / 'partA_maps.png'}")


if __name__ == "__main__":
    main()
