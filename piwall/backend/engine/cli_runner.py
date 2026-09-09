#!/usr/bin/env python3
"""CLI race runner for PIT WALL — Phase 2 validation.

Runs a complete race with all 5 built-in bots at Bahrain and prints
a detailed race report.
"""

import sys
import os
import json

# Support both direct execution and module import
try:
    from ..data.tracks import TRACKS
    from .build import build_engine, build_track_physics
    from .race import Decision
    from .bots import BUILTIN_BOTS
except ImportError:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
    from piwall.backend.data.tracks import TRACKS
    from piwall.backend.engine.build import build_engine, build_track_physics
    from piwall.backend.engine.race import Decision
    from piwall.backend.engine.bots import BUILTIN_BOTS


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
    engine = build_engine(track_name, seed, track_physics=track)

    # Add all 5 built-in bots
    for pos, (bot_id, bot_info) in enumerate(BUILTIN_BOTS.items(), 1):
        engine.add_car(
            car_id=bot_id,
            player_id=bot_id,
            strategy=bot_info["strategy"],
            starting_position=pos,
            starting_compound=bot_info["starting_compound"],
            # Deliberately unlike the match job and the replay runner, which
            # both leave this None and get belief.DEFAULT_TYPICAL_STINTS.
            # This CLI is a calibration-inspection tool: seeding the belief
            # models with the track's own stint lengths is what makes its
            # report worth reading. Passing it in production would change
            # every rival-pit prediction and so every race outcome, which is
            # a game-balance decision, not a determinism fix -- so the two
            # reproducible paths stay identical to each other and this one
            # stays explicitly different.
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
