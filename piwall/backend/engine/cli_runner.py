#!/usr/bin/env python3
"""CLI race runner for PIT WALL — Phase 2 validation.

Runs a complete race with all 5 built-in bots at Bahrain and prints
a detailed race report.
"""

import sys
import os
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from piwall.backend.data.calibration import calibrate_track, _deg_model
from piwall.backend.data.tracks import TRACKS
from piwall.backend.engine.physics import TyreModel, TrackPhysics
from piwall.backend.engine.race import RaceEngine, Decision
from piwall.backend.engine.bots import BUILTIN_BOTS


def build_track_physics(track_name: str) -> TrackPhysics:
    """Build TrackPhysics from calibrated data."""
    track_cfg = TRACKS[track_name]
    cal = calibrate_track(track_name)

    tyre_models = {}
    for compound, params in cal.compounds.items():
        tyre_models[compound] = TyreModel(
            compound=compound,
            alpha=params.alpha,
            k=params.k,
            e=params.e,
            base_lap_time=params.base_lap_time,
        )

    # Add fallback compounds if missing
    for fallback in ["SOFT", "MEDIUM", "HARD"]:
        if fallback not in tyre_models:
            # Interpolate from available data
            if tyre_models:
                ref = list(tyre_models.values())[0]
                offsets = {"SOFT": -0.4, "MEDIUM": 0.0, "HARD": 0.5}
                k_mults = {"SOFT": 1.5, "MEDIUM": 1.0, "HARD": 0.7}
                tyre_models[fallback] = TyreModel(
                    compound=fallback,
                    alpha=ref.alpha + offsets.get(fallback, 0),
                    k=ref.k * k_mults.get(fallback, 1.0),
                    e=ref.e,
                    base_lap_time=ref.base_lap_time + offsets.get(fallback, 0) * 0.3,
                )

    # Add wet compound fallbacks
    if "INTERMEDIATE" not in tyre_models:
        ref = tyre_models.get("MEDIUM", list(tyre_models.values())[0])
        tyre_models["INTERMEDIATE"] = TyreModel(
            compound="INTERMEDIATE",
            alpha=ref.alpha + 3.0,
            k=ref.k * 0.5,
            e=ref.e,
            base_lap_time=ref.base_lap_time + 3.0,
        )

    return TrackPhysics(
        name=track_name,
        base_lap_time=cal.base_lap_time,
        pit_loss_seconds=cal.pit_loss_seconds,
        total_laps=track_cfg.total_laps,
        drs_zones=track_cfg.drs_zones,
        overtake_difficulty=track_cfg.overtake_difficulty,
        fuel_load_kg=track_cfg.fuel_load_kg,
        tyre_models=tyre_models,
    )


def print_race_report(result):
    """Print a formatted race report."""
    print("\n" + "=" * 80)
    print(f"  RACE RESULT — {result.track.upper()} ({result.total_laps} laps)")
    print("=" * 80)

    # Final standings
    print(f"\n{'Pos':>3s}  {'Car':<8s}  {'Total Time':>12s}  {'Gap':>10s}  "
          f"{'Stops':>5s}  {'Pit Laps':<20s}  {'Compounds':<20s}  {'Status':<8s}")
    print("-" * 95)

    leader_time = None
    for car in result.final_standings:
        if not car.retired and leader_time is None:
            leader_time = car.total_time

        pos = f"P{car.position}"
        total = f"{car.total_time:.3f}s" if not car.retired else "DNF"
        if car.retired:
            gap = "DNF"
        elif leader_time and car.total_time > leader_time:
            gap = f"+{car.total_time - leader_time:.3f}s"
        else:
            gap = "LEADER"

        pits = str(car.pit_count)
        pit_laps = ", ".join(str(l) for l in car.pit_laps) if car.pit_laps else "-"
        compounds = " → ".join(car.compounds_used)
        status = "RET" if car.retired else "FIN"

        print(f"{pos:>3s}  {car.car_id:<8s}  {total:>12s}  {gap:>10s}  "
              f"{pits:>5s}  {pit_laps:<20s}  {compounds:<20s}  {status:<8s}")

    # Key events
    print(f"\n{'─' * 80}")
    print("KEY EVENTS:")
    print(f"{'─' * 80}")
    for event in result.events:
        icon = {
            "pit": "🔧",
            "sc_start": "🟡",
            "sc_end": "🟢",
            "dnf": "💥",
            "overtake": "⚔️ ",
            "weather": "🌧️ ",
            "penalty": "⚠️ ",
            "undercut": "🎯",
        }.get(event.event_type, "•")
        print(f"  Lap {event.lap:>2d}  {icon}  {event.detail}")

    # Weather summary
    weather_changes = []
    prev = result.weather_history[0]
    for i, w in enumerate(result.weather_history[1:], 1):
        if w != prev:
            weather_changes.append(f"Lap {i}: {prev}→{w}")
            prev = w
    if weather_changes:
        print(f"\nWeather changes: {', '.join(weather_changes)}")
    else:
        print(f"\nWeather: {result.weather_history[0]} throughout")

    # Lap time ranges
    print(f"\n{'─' * 80}")
    print("LAP TIME RANGES (fastest / slowest clean lap):")
    for car in result.final_standings:
        if car.retired:
            continue
        car_laps = [
            ld for ld in result.lap_data
            for cd in ld["cars"]
            if cd["car_id"] == car.car_id and cd["last_lap_time"] > 0
            and not ld["safety_car"]
        ]
        if car_laps:
            times = [
                cd["last_lap_time"]
                for ld in car_laps
                for cd in ld["cars"]
                if cd["car_id"] == car.car_id
            ]
            if times:
                print(f"  {car.car_id:<8s}: {min(times):.3f}s — {max(times):.3f}s "
                      f"(range: {max(times) - min(times):.3f}s)")


def main():
    track_name = sys.argv[1] if len(sys.argv) > 1 else "bahrain"
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else 42

    print("=" * 80)
    print(f"  PIT WALL — Phase 2 Race Simulation")
    print(f"  Track: {TRACKS[track_name].display_name}")
    print(f"  Seed: {seed}")
    print("=" * 80)

    # Build track physics from calibrated data
    print("\nLoading track data...")
    track = build_track_physics(track_name)
    print(f"  Base lap: {track.base_lap_time:.3f}s")
    print(f"  Pit loss: {track.pit_loss_seconds:.1f}s")
    print(f"  Compounds: {list(track.tyre_models.keys())}")

    # Create race engine
    track_cfg = TRACKS[track_name]
    engine = RaceEngine(
        track=track,
        weather_transitions=track_cfg.weather_transitions,
        initial_weather="dry",
        seed=seed,
        sc_prob_dry=track_cfg.safety_car_prob_dry,
        sc_prob_wet=track_cfg.safety_car_prob_wet,
    )

    # Add all 5 built-in bots
    for pos, (bot_id, bot_info) in enumerate(BUILTIN_BOTS.items(), 1):
        engine.add_car(
            car_id=bot_id,
            player_id=bot_id,
            strategy=bot_info["strategy"],
            starting_position=pos,
            starting_compound=bot_info["starting_compound"],
            typical_stints=track_cfg.typical_stint,
        )

    # Run the race
    print(f"\nStarting {track_cfg.total_laps}-lap race...")
    print("(Running simulation...)\n")

    result = engine.run()

    # Print report
    print_race_report(result)

    print(f"\nTotal events: {len(result.events)}")
    print(f"Total lap records: {len(result.lap_data)}")


if __name__ == "__main__":
    main()
