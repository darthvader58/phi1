"""FastF1 data fetching and caching for PIT WALL.

Pulls real F1 telemetry data, extracts stint information,
and provides lap time data grouped by compound.
"""

import os
import pickle
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import fastf1
import numpy as np
import pandas as pd

# Set up FastF1 cache directory
CACHE_DIR = Path(__file__).parent.parent.parent / "fastf1_cache"
CACHE_DIR.mkdir(exist_ok=True)
fastf1.Cache.enable_cache(str(CACHE_DIR))

# Local processed data cache
PROCESSED_CACHE_DIR = Path(__file__).parent.parent.parent / "processed_cache"
PROCESSED_CACHE_DIR.mkdir(exist_ok=True)


def _cache_path(year: int, event: str) -> Path:
    return PROCESSED_CACHE_DIR / f"{year}_{event.replace(' ', '_')}_stints.pkl"


def load_race_data(year: int, event: str, force_reload: bool = False) -> dict:
    """Load race data from FastF1, using local cache if available.

    Returns a dict with:
        - 'laps': DataFrame of all laps with stint/compound info
        - 'pit_stops': DataFrame of pit stop data
        - 'event_info': dict with event metadata
    """
    cache_file = _cache_path(year, event)

    if cache_file.exists() and not force_reload:
        with open(cache_file, "rb") as f:
            return pickle.load(f)

    print(f"Loading {year} {event} race data from FastF1...")
    session = fastf1.get_session(year, event, "R")
    session.load()

    laps = session.laps.copy()

    # Extract key columns
    laps["LapTimeSeconds"] = laps["LapTime"].dt.total_seconds()

    # Get pit stop info
    pit_data = laps[laps["PitInTime"].notna()][
        ["Driver", "LapNumber", "Compound", "Stint", "LapTimeSeconds"]
    ].copy()

    total_laps = int(laps["LapNumber"].max())

    result = {
        "laps": laps,
        "pit_stops": pit_data,
        "event_info": {
            "year": year,
            "event": event,
            "total_laps": total_laps,
            "drivers": list(laps["Driver"].unique()),
        },
    }

    with open(cache_file, "wb") as f:
        pickle.dump(result, f)

    print(f"Cached processed data to {cache_file}")
    return result


def extract_stint_data(race_data: dict) -> pd.DataFrame:
    """Extract clean stint data from race laps.

    Returns a DataFrame with columns:
        Driver, Compound, StintNumber, TyreAge, LapTimeSeconds, LapNumber,
        FuelCorrectedTime
    Filters out:
        - In/out laps (pit entry/exit laps)
        - Safety car / yellow flag laps (TrackStatus containing '4', '5', '6', '7')
        - Laps with missing times
        - First lap (formation chaos)
        - Obvious outlier laps (> median + 3s for that stint)
    """
    laps = race_data["laps"].copy()
    total_laps = race_data["event_info"]["total_laps"]

    # Filter to valid laps
    mask = (
        laps["LapTimeSeconds"].notna()
        & ~laps["PitInTime"].notna()
        & ~laps["PitOutTime"].notna()
        & (laps["LapTimeSeconds"] > 0)
        & (laps["LapNumber"] > 1)  # Skip lap 1
    )
    valid = laps[mask].copy()

    # Remove safety car / VSC / yellow laps
    if "TrackStatus" in valid.columns:
        ts = valid["TrackStatus"].astype(str)
        sc_mask = ts.str.contains("[4567]", regex=True, na=False)
        valid = valid[~sc_mask].copy()

    # Use FastF1's TyreLife if available, otherwise compute
    if "TyreLife" in valid.columns and valid["TyreLife"].notna().sum() > 0:
        valid["TyreAge"] = valid["TyreLife"].astype(int)
    else:
        valid["TyreAge"] = valid.groupby(["Driver", "Stint"]).cumcount() + 1

    # Fuel correction: assume linear burn from 110kg to 0kg over race distance
    # delta_fuel = 0.032 * fuel_kg, fuel_kg decreases linearly
    fuel_per_lap = 110.0 / total_laps
    valid["FuelKg"] = 110.0 - (valid["LapNumber"] - 1) * fuel_per_lap
    valid["FuelDelta"] = 0.032 * valid["FuelKg"]
    # Fuel-corrected time: remove the fuel effect to isolate tyre degradation
    valid["FuelCorrectedTime"] = valid["LapTimeSeconds"] - valid["FuelDelta"]

    # Remove outliers per driver-stint (laps > median + 3s)
    medians = valid.groupby(["Driver", "Stint"])["LapTimeSeconds"].transform("median")
    valid = valid[valid["LapTimeSeconds"] <= medians + 3.0]

    result = valid[
        ["Driver", "Compound", "Stint", "TyreAge", "LapTimeSeconds",
         "LapNumber", "FuelCorrectedTime"]
    ].copy()
    result = result.rename(columns={"Stint": "StintNumber"})
    result = result.reset_index(drop=True)

    return result


def get_compound_lap_times(stint_data: pd.DataFrame) -> Dict[str, pd.DataFrame]:
    """Group stint data by compound.

    Returns dict mapping compound name to DataFrame of
    (TyreAge, LapTimeSeconds, FuelCorrectedTime).
    """
    compounds = {}
    for compound in stint_data["Compound"].unique():
        if compound in ("SOFT", "MEDIUM", "HARD", "INTERMEDIATE", "WET"):
            cols = ["TyreAge", "LapTimeSeconds"]
            if "FuelCorrectedTime" in stint_data.columns:
                cols.append("FuelCorrectedTime")
            c_data = stint_data[stint_data["Compound"] == compound][cols].copy()
            compounds[compound] = c_data.sort_values("TyreAge").reset_index(drop=True)
    return compounds


def get_pit_loss(race_data: dict) -> float:
    """Estimate pit lane time loss from actual pit stop data.

    Accounts for both the pit-in lap (slower due to pit entry) and the
    pit-out lap (slower due to pit exit) vs normal racing laps.
    """
    laps = race_data["laps"].copy()
    laps = laps.sort_values(["Driver", "LapNumber"])

    pit_in_laps = laps[laps["PitInTime"].notna()].copy()
    if pit_in_laps.empty:
        return 22.0

    losses = []
    for _, pit_lap in pit_in_laps.iterrows():
        driver = pit_lap["Driver"]
        lap_num = pit_lap["LapNumber"]
        pit_in_time = pit_lap["LapTimeSeconds"]

        # Get the pit-out lap (next lap)
        next_lap = laps[
            (laps["Driver"] == driver) & (laps["LapNumber"] == lap_num + 1)
        ]
        if next_lap.empty:
            continue
        pit_out_time = next_lap["LapTimeSeconds"].values[0]

        if pd.isna(pit_in_time) or pd.isna(pit_out_time):
            continue

        # Get normal laps for reference (before pit-in, not pit laps themselves)
        normal = laps[
            (laps["Driver"] == driver)
            & (laps["LapNumber"].between(lap_num - 4, lap_num - 1))
            & laps["PitInTime"].isna()
            & laps["PitOutTime"].isna()
            & laps["LapTimeSeconds"].notna()
        ]

        if len(normal) >= 2:
            normal_pace = normal["LapTimeSeconds"].median()
            # Total time for pit-in + pit-out laps vs 2 normal laps
            loss = (pit_in_time + pit_out_time) - (2 * normal_pace)
            if 10.0 < loss < 40.0:  # Sanity bounds
                losses.append(loss)

    if losses:
        return float(np.median(losses))
    return 22.0
