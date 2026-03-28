#!/usr/bin/env python3
"""Debug script to inspect raw FastF1 data quality."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pandas as pd
pd.set_option("display.max_columns", 20)
pd.set_option("display.width", 200)

from piwall.backend.data.fastf1_loader import load_race_data

race_data = load_race_data(2024, "Bahrain")
laps = race_data["laps"]

print("=== COLUMNS ===")
print(list(laps.columns))

print("\n=== COMPOUND VALUE COUNTS ===")
print(laps["Compound"].value_counts())

print("\n=== TRACK STATUS VALUES ===")
if "TrackStatus" in laps.columns:
    print(laps["TrackStatus"].value_counts())

print("\n=== SAMPLE LAPS (first driver, first 10 laps) ===")
driver = laps["Driver"].iloc[0]
sample = laps[laps["Driver"] == driver].head(10)
print(sample[["Driver", "LapNumber", "Compound", "Stint", "LapTimeSeconds", "PitInTime", "PitOutTime", "TrackStatus"]].to_string())

print("\n=== PIT LAPS (first 5) ===")
pit_in = laps[laps["PitInTime"].notna()].head(5)
print(pit_in[["Driver", "LapNumber", "Compound", "LapTimeSeconds"]].to_string())

pit_out = laps[laps["PitOutTime"].notna()].head(5)
print("\n=== PIT OUT LAPS (first 5) ===")
print(pit_out[["Driver", "LapNumber", "Compound", "LapTimeSeconds"]].to_string())

# Check what filtering does
valid = laps[laps["LapTimeSeconds"].notna() & (laps["LapTimeSeconds"] > 0)].copy()
print(f"\n=== Total laps with valid times: {len(valid)} ===")
no_pit = valid[valid["PitInTime"].isna() & valid["PitOutTime"].isna()]
print(f"After removing pit in/out laps: {len(no_pit)}")

if "TrackStatus" in no_pit.columns:
    ts_filter = no_pit[no_pit["TrackStatus"].astype(str).str.contains("1") | no_pit["TrackStatus"].isna()]
    print(f"After TrackStatus filter: {len(ts_filter)}")

# Real pit loss: compare pit in-lap time + pit out-lap time vs 2 normal laps
print("\n=== PIT LOSS ESTIMATION ===")
for driver in laps["Driver"].unique()[:3]:
    d_laps = laps[laps["Driver"] == driver].sort_values("LapNumber")
    pit_in_laps = d_laps[d_laps["PitInTime"].notna()]
    for _, row in pit_in_laps.iterrows():
        lap_num = row["LapNumber"]
        # Get the pit in lap and pit out lap (next lap)
        pit_in_time = row["LapTimeSeconds"]
        next_lap = d_laps[d_laps["LapNumber"] == lap_num + 1]
        pit_out_time = next_lap["LapTimeSeconds"].values[0] if len(next_lap) > 0 else None

        # Normal laps before
        normal = d_laps[(d_laps["LapNumber"].between(lap_num - 3, lap_num - 1)) &
                        d_laps["PitInTime"].isna() & d_laps["PitOutTime"].isna() &
                        d_laps["LapTimeSeconds"].notna()]
        if len(normal) > 0 and pit_in_time and pit_out_time:
            normal_pace = normal["LapTimeSeconds"].median()
            total_pit_laps = pit_in_time + pit_out_time
            total_normal = normal_pace * 2
            loss = total_pit_laps - total_normal
            print(f"  {driver} L{int(lap_num)}: pit_in={pit_in_time:.1f}s pit_out={pit_out_time:.1f}s normal={normal_pace:.1f}s loss={loss:.1f}s")
