"""Tyre degradation calibration module.

Fits the power-law tyre degradation model from real FastF1 stint data:
    delta_tyre(compound, age) = alpha + k * age^e

Where:
    alpha = base compound offset from track base time (intrinsic compound speed)
    k = degradation rate coefficient
    e = degradation exponent (typically 1.0-2.0)

IMPORTANT: We fit against fuel-corrected lap times to isolate the tyre
degradation signal from the fuel burn-off effect.
"""

import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
from scipy.optimize import curve_fit

from .fastf1_loader import (
    load_race_data,
    extract_stint_data,
    get_compound_lap_times,
    get_pit_loss,
)
from .tracks import TRACKS, TrackConfig

CALIBRATION_CACHE = Path(__file__).parent.parent.parent / "processed_cache"


@dataclass
class TyreDegParams:
    """Fitted parameters for a single compound at a single track."""
    compound: str
    track: str
    alpha: float   # Base compound offset (seconds from track base time)
    k: float       # Degradation rate coefficient
    e: float       # Degradation exponent
    r_squared: float  # Goodness of fit
    n_samples: int    # Number of data points used
    base_lap_time: float  # Estimated base fuel-corrected lap time


@dataclass
class TrackCalibration:
    """Full calibration result for a track."""
    track: str
    base_lap_time: float
    pit_loss_seconds: float
    compounds: Dict[str, TyreDegParams]


def _deg_model(age: np.ndarray, alpha: float, k: float, e: float) -> np.ndarray:
    """Power-law degradation: delta = alpha + k * age^e"""
    return alpha + k * np.power(age, e)


def fit_compound(
    tyre_ages: np.ndarray,
    lap_times: np.ndarray,
    compound: str,
    track: str,
) -> Optional[TyreDegParams]:
    """Fit the tyre degradation model for a single compound.

    Args:
        tyre_ages: Array of tyre ages (laps on tyre)
        lap_times: Array of fuel-corrected lap times in seconds
        compound: Compound name (SOFT/MEDIUM/HARD)
        track: Track name

    Returns:
        TyreDegParams with fitted parameters, or None if fit fails
    """
    if len(tyre_ages) < 5:
        print(f"  {compound}: Too few data points ({len(tyre_ages)}), skipping")
        return None

    # Estimate base lap time from early laps (age 1-3)
    early_mask = tyre_ages <= 3
    if early_mask.sum() > 0:
        base_estimate = np.percentile(lap_times[early_mask], 15)
    else:
        base_estimate = np.min(lap_times)

    # Compute delta from base (degradation signal)
    deltas = lap_times - base_estimate

    # Initial parameter guesses
    p0 = [0.0, 0.03, 1.2]
    bounds = (
        [-2.0, 0.0001, 0.5],   # Lower bounds
        [5.0, 1.0, 3.0],        # Upper bounds
    )

    try:
        popt, pcov = curve_fit(
            _deg_model,
            tyre_ages.astype(float),
            deltas,
            p0=p0,
            bounds=bounds,
            maxfev=10000,
        )
        alpha, k, e = popt

        # Compute R-squared
        predicted = _deg_model(tyre_ages.astype(float), *popt)
        ss_res = np.sum((deltas - predicted) ** 2)
        ss_tot = np.sum((deltas - np.mean(deltas)) ** 2)
        r_squared = 1 - (ss_res / ss_tot) if ss_tot > 0 else 0.0

        return TyreDegParams(
            compound=compound,
            track=track,
            alpha=round(float(alpha), 4),
            k=round(float(k), 6),
            e=round(float(e), 4),
            r_squared=round(float(r_squared), 4),
            n_samples=len(tyre_ages),
            base_lap_time=round(float(base_estimate), 3),
        )

    except (RuntimeError, ValueError) as ex:
        print(f"  {compound}: Curve fit failed: {ex}")
        return None


def calibrate_track(track_name: str, force_reload: bool = False) -> TrackCalibration:
    """Run full calibration for a track using real FastF1 data.

    Uses fuel-corrected lap times to isolate tyre degradation.
    """
    track_cfg = TRACKS[track_name]
    print(f"\n{'='*60}")
    print(f"Calibrating: {track_cfg.display_name} ({track_cfg.fastf1_year})")
    print(f"{'='*60}")

    # Load raw data
    race_data = load_race_data(
        track_cfg.fastf1_year,
        track_cfg.fastf1_event,
        force_reload=force_reload,
    )

    # Extract clean stint data (includes fuel correction)
    stint_data = extract_stint_data(race_data)
    print(f"Clean stint laps: {len(stint_data)}")

    # Get compound-grouped data
    compound_data = get_compound_lap_times(stint_data)
    print(f"Compounds found: {list(compound_data.keys())}")

    # Get actual pit loss
    pit_loss = get_pit_loss(race_data)
    print(f"Estimated pit loss: {pit_loss:.1f}s")

    # Fit each compound using FUEL-CORRECTED times
    fitted = {}
    for compound, data in compound_data.items():
        if compound not in ("SOFT", "MEDIUM", "HARD"):
            continue
        ages = data["TyreAge"].values
        # Use fuel-corrected times for fitting
        if "FuelCorrectedTime" in data.columns:
            times = data["FuelCorrectedTime"].values
        else:
            times = data["LapTimeSeconds"].values
        print(f"\nFitting {compound} ({len(ages)} samples, age range {ages.min()}-{ages.max()})...")

        params = fit_compound(ages, times, compound, track_name)
        if params:
            fitted[compound] = params
            print(f"  alpha={params.alpha:.4f}, k={params.k:.6f}, e={params.e:.4f}")
            print(f"  R²={params.r_squared:.4f}, base_lap={params.base_lap_time:.3f}s")

    # Estimate overall base lap time from the fastest compound's base
    base_times = [p.base_lap_time for p in fitted.values()]
    overall_base = min(base_times) if base_times else track_cfg.base_lap_time

    result = TrackCalibration(
        track=track_name,
        base_lap_time=round(overall_base, 3),
        pit_loss_seconds=round(pit_loss, 1),
        compounds=fitted,
    )

    # Cache calibration result
    cache_file = CALIBRATION_CACHE / f"calibration_{track_name}.json"
    save_data = {
        "track": result.track,
        "base_lap_time": result.base_lap_time,
        "pit_loss_seconds": result.pit_loss_seconds,
        "compounds": {k: asdict(v) for k, v in result.compounds.items()},
    }
    with open(cache_file, "w") as f:
        json.dump(save_data, f, indent=2)
    print(f"\nSaved calibration to {cache_file}")

    return result


def predict_lap_time(
    params: TyreDegParams,
    tyre_age: int,
    fuel_kg: float = 55.0,
) -> float:
    """Predict a lap time using the fitted model.

    T(lap) = base_lap_time + delta_tyre(compound, age) + delta_fuel(fuel_kg)
    delta_fuel = 0.032 * fuel_kg
    """
    delta_tyre = _deg_model(np.array([tyre_age], dtype=float), params.alpha, params.k, params.e)[0]
    delta_fuel = 0.032 * fuel_kg
    return params.base_lap_time + delta_tyre + delta_fuel


def print_calibration_summary(cal: TrackCalibration) -> None:
    """Print a nicely formatted calibration summary."""
    print(f"\n{'='*60}")
    print(f"CALIBRATION SUMMARY: {cal.track.upper()}")
    print(f"{'='*60}")
    print(f"Base lap time (fuel-corrected): {cal.base_lap_time:.3f}s")
    print(f"Pit loss:                       {cal.pit_loss_seconds:.1f}s")
    print()

    for compound in ["SOFT", "MEDIUM", "HARD"]:
        if compound in cal.compounds:
            p = cal.compounds[compound]
            print(f"  {compound:8s}: alpha={p.alpha:+.4f}  k={p.k:.6f}  e={p.e:.4f}  "
                  f"R²={p.r_squared:.4f}  (n={p.n_samples})")

            # Print predicted degradation at key ages
            ages = [1, 5, 10, 15, 20, 25, 30]
            deltas = [_deg_model(np.array([a], dtype=float), p.alpha, p.k, p.e)[0] for a in ages]
            print(f"           Deg @ ages {ages}:")
            print(f"           {[f'{d:.3f}s' for d in deltas]}")
        else:
            print(f"  {compound:8s}: NO DATA")
    print()
