#!/usr/bin/env python3
"""Phase 1 validation: Calibrate tyre degradation for the 2024 Bahrain GP.

Pulls real data via FastF1, fits the power-law degradation model
against fuel-corrected lap times, prints fitted parameters, and plots curves.
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from piwall.backend.data.calibration import (
    calibrate_track,
    print_calibration_summary,
    _deg_model,
)
from piwall.backend.data.fastf1_loader import (
    load_race_data,
    extract_stint_data,
    get_compound_lap_times,
)
from piwall.backend.data.tracks import TRACKS


def plot_tyre_deg_curves(cal, compound_data, output_path="bahrain_tyre_deg.png"):
    """Plot fitted tyre degradation curves against real fuel-corrected data."""
    compound_colors = {
        "SOFT": "#FF3333",
        "MEDIUM": "#FFD700",
        "HARD": "#CCCCCC",
    }

    available = [c for c in ["SOFT", "MEDIUM", "HARD"]
                 if c in compound_data and c in cal.compounds]
    n_plots = max(len(available), 1)

    fig, axes_arr = plt.subplots(1, n_plots, figsize=(7 * n_plots, 6), sharey=True)
    if n_plots == 1:
        axes_arr = [axes_arr]

    fig.patch.set_facecolor("#0f0f23")

    for idx, compound in enumerate(available):
        ax = axes_arr[idx]
        color = compound_colors[compound]
        data = compound_data[compound]
        params = cal.compounds[compound]

        # Use fuel-corrected times
        time_col = "FuelCorrectedTime" if "FuelCorrectedTime" in data.columns else "LapTimeSeconds"

        # Scatter plot of real data
        ax.scatter(
            data["TyreAge"],
            data[time_col],
            alpha=0.35,
            s=12,
            color=color,
            label="Real laps (fuel-corrected)",
            zorder=2,
        )

        # Fitted curve
        max_age = int(data["TyreAge"].max())
        age_range = np.linspace(1, max_age, 200)
        predicted_deltas = _deg_model(age_range, params.alpha, params.k, params.e)
        predicted_times = params.base_lap_time + predicted_deltas

        ax.plot(
            age_range,
            predicted_times,
            color=color,
            linewidth=2.5,
            label=(f"Fit: α={params.alpha:.3f}, k={params.k:.4f}, "
                   f"e={params.e:.2f}\nR²={params.r_squared:.3f}"),
            zorder=3,
        )

        ax.set_title(f"{compound} (n={params.n_samples})", color="white", fontsize=13)
        ax.set_xlabel("Tyre Age (laps)", color="white")
        if idx == 0:
            ax.set_ylabel("Fuel-Corrected Lap Time (s)", color="white")
        ax.legend(fontsize=9, facecolor="#1a1a2e", edgecolor="#333",
                  labelcolor="white", loc="upper left")
        ax.grid(True, alpha=0.2, color="#555")
        ax.set_facecolor("#1a1a2e")
        ax.tick_params(colors="white")
        for spine in ax.spines.values():
            spine.set_color("#333")

    fig.suptitle(
        f"PIT WALL — Tyre Degradation: {TRACKS['bahrain'].display_name} 2024\n"
        f"(fuel effect removed, pit loss={cal.pit_loss_seconds:.1f}s)",
        fontsize=14, fontweight="bold", color="white",
    )

    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    print(f"\nPlot saved to: {output_path}")
    plt.close()


def main():
    print("=" * 60)
    print("PIT WALL — Phase 1: FastF1 Data Calibration")
    print("Track: 2024 Bahrain Grand Prix")
    print("=" * 60)

    # Delete stale cache to force re-extraction with new logic
    from piwall.backend.data.fastf1_loader import PROCESSED_CACHE_DIR
    stale = PROCESSED_CACHE_DIR / "2024_Bahrain_stints.pkl"
    if stale.exists():
        stale.unlink()
        print("(cleared stale processed cache)")

    # Step 1: Calibrate
    cal = calibrate_track("bahrain", force_reload=True)

    # Step 2: Print summary
    print_calibration_summary(cal)

    # Step 3: Load raw data for plotting
    track_cfg = TRACKS["bahrain"]
    race_data = load_race_data(track_cfg.fastf1_year, track_cfg.fastf1_event)
    stint_data = extract_stint_data(race_data)
    compound_data = get_compound_lap_times(stint_data)

    # Step 4: Plot
    output_path = os.path.join(os.path.dirname(__file__), "..", "..", "bahrain_tyre_deg.png")
    output_path = os.path.abspath(output_path)
    plot_tyre_deg_curves(cal, compound_data, output_path)

    # Step 5: Print predicted lap times at various ages
    print("\n" + "=" * 60)
    print("PREDICTED LAP TIMES (fuel-corrected base + tyre deg only)")
    print("=" * 60)
    compounds = [c for c in ["SOFT", "MEDIUM", "HARD"] if c in cal.compounds]
    header = f"{'Age':>4s}" + "".join(f"  {c:>10s}" for c in compounds)
    print(header)
    print("-" * (4 + 12 * len(compounds)))
    for age in [1, 3, 5, 8, 10, 12, 15, 18, 20, 25, 30]:
        row = f"{age:4d}"
        for compound in compounds:
            p = cal.compounds[compound]
            delta = _deg_model(np.array([age], dtype=float), p.alpha, p.k, p.e)[0]
            row += f"  {delta:10.3f}s"
        print(row)

    print("\nNote: These are delta_tyre values (seconds added to base).")
    print("Full lap time = base + delta_tyre + 0.032*fuel_kg + N(0, 0.15)")
    print("\nPhase 1 calibration complete.")
    return cal


if __name__ == "__main__":
    main()
