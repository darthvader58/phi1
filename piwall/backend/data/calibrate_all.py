#!/usr/bin/env python3
"""Calibrate all 6 tracks and produce a summary report."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from piwall.backend.data.calibration import calibrate_track, print_calibration_summary, _deg_model
from piwall.backend.data.fastf1_loader import load_race_data, extract_stint_data, get_compound_lap_times
from piwall.backend.data.tracks import TRACKS

COMPOUND_COLORS = {"SOFT": "#FF3333", "MEDIUM": "#FFD700", "HARD": "#CCCCCC"}

def main():
    all_tracks = ["bahrain", "monaco", "monza", "spa", "silverstone", "suzuka"]
    results = {}

    for track_name in all_tracks:
        try:
            cal = calibrate_track(track_name)
            print_calibration_summary(cal)
            results[track_name] = cal
        except Exception as e:
            print(f"\n*** FAILED: {track_name} — {e}")
            results[track_name] = None

    # Summary table
    print("\n" + "=" * 90)
    print("ALL TRACKS SUMMARY")
    print("=" * 90)
    print(f"{'Track':<14s} {'Pit Loss':>8s} {'Base':>7s}  {'SOFT (α/k/e/R²)':>28s}  {'MED (α/k/e/R²)':>28s}  {'HARD (α/k/e/R²)':>28s}")
    print("-" * 130)
    for name in all_tracks:
        cal = results[name]
        if cal is None:
            print(f"{name:<14s}  FAILED")
            continue
        row = f"{name:<14s} {cal.pit_loss_seconds:>7.1f}s {cal.base_lap_time:>6.1f}s"
        for comp in ["SOFT", "MEDIUM", "HARD"]:
            if comp in cal.compounds:
                p = cal.compounds[comp]
                row += f"  {p.alpha:+.2f}/{p.k:.4f}/{p.e:.2f}/{p.r_squared:.2f}"
            else:
                row += f"  {'— NO DATA —':>28s}"
        print(row)

    # Plot all tracks
    fig, axes = plt.subplots(2, 3, figsize=(21, 12))
    fig.patch.set_facecolor("#0f0f23")

    for idx, track_name in enumerate(all_tracks):
        ax = axes[idx // 3][idx % 3]
        cal = results[track_name]
        if cal is None:
            ax.set_title(f"{track_name.upper()} — FAILED", color="red")
            ax.set_facecolor("#1a1a2e")
            continue

        track_cfg = TRACKS[track_name]
        try:
            race_data = load_race_data(track_cfg.fastf1_year, track_cfg.fastf1_event)
            stint_data = extract_stint_data(race_data)
            compound_data = get_compound_lap_times(stint_data)
        except Exception:
            compound_data = {}

        for comp in ["SOFT", "MEDIUM", "HARD"]:
            if comp in cal.compounds and comp in compound_data:
                p = cal.compounds[comp]
                data = compound_data[comp]
                color = COMPOUND_COLORS[comp]
                time_col = "FuelCorrectedTime" if "FuelCorrectedTime" in data.columns else "LapTimeSeconds"

                ax.scatter(data["TyreAge"], data[time_col], alpha=0.2, s=6, color=color)
                ages = np.linspace(1, data["TyreAge"].max(), 100)
                fitted = p.base_lap_time + _deg_model(ages, p.alpha, p.k, p.e)
                ax.plot(ages, fitted, color=color, linewidth=2, label=f"{comp} R²={p.r_squared:.2f}")

        ax.set_title(f"{track_name.upper()} (pit={cal.pit_loss_seconds:.0f}s)", color="white", fontsize=11)
        ax.set_xlabel("Tyre Age", color="white", fontsize=9)
        ax.set_ylabel("Lap Time (s)", color="white", fontsize=9)
        ax.legend(fontsize=7, facecolor="#1a1a2e", edgecolor="#333", labelcolor="white")
        ax.grid(True, alpha=0.2, color="#555")
        ax.set_facecolor("#1a1a2e")
        ax.tick_params(colors="white", labelsize=8)
        for spine in ax.spines.values():
            spine.set_color("#333")

    fig.suptitle("PIT WALL — Tyre Degradation Calibration (All 6 Tracks, 2024 Data)",
                 fontsize=15, fontweight="bold", color="white")
    plt.tight_layout()
    out = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "all_tracks_tyre_deg.png"))
    plt.savefig(out, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    print(f"\nPlot saved to: {out}")
    plt.close()

if __name__ == "__main__":
    main()
