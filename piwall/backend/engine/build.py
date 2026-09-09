"""The one place a race is assembled: track physics, then the RaceEngine.

Three call sites used to construct a RaceEngine by hand -- the production
match job, the replay runner and the dev CLI -- and they disagreed. The
replay runner passed neither `weather_transitions` nor the per-track
safety-car probabilities, so every replay silently fell back to
`weather.DEFAULT_TRANSITIONS` (dry->dry 0.92) and to RaceEngine's own
default SC odds, instead of the track's table (bahrain: dry->dry 0.998,
"desert, rain is extremely rare"). Replays therefore raced through weather
the game never produces: a different race from the one being replayed,
which is exactly what the determinism contract forbids.

Every caller now goes through build_engine, so a new per-track parameter
cannot reach production while leaving replay behind.
"""

from typing import Optional

from ..data.calibration_store import load_calibration
from ..data.tracks import TRACKS
from .physics import TrackPhysics, TyreModel
from .race import RaceEngine

# Compound speed hierarchy: SOFT fastest, HARD slowest
COMPOUND_ALPHA = {"SOFT": 0.0, "MEDIUM": 0.35, "HARD": 0.7}
# Compound deg multipliers: SOFT degrades most, HARD least
COMPOUND_K_SCALE = {"SOFT": 1.5, "MEDIUM": 1.0, "HARD": 0.65}


def build_track_physics(track_name: str) -> TrackPhysics:
    """Build TrackPhysics from the frozen calibration artifact."""
    track_cfg = TRACKS[track_name]
    cal = load_calibration(track_name)

    tyre_models = {}

    # Use average calibrated exponent and k as reference, then scale per compound
    cal_compounds = list(cal.compounds.values())
    ref_e = sum(p.e for p in cal_compounds) / len(cal_compounds) if cal_compounds else 1.1
    ref_k = sum(p.k for p in cal_compounds) / len(cal_compounds) if cal_compounds else 0.05

    for compound in cal.compounds:
        tyre_models[compound] = TyreModel(
            compound=compound,
            alpha=COMPOUND_ALPHA.get(compound, 0.35),
            k=ref_k * COMPOUND_K_SCALE.get(compound, 1.0),
            e=ref_e,
            base_lap_time=cal.base_lap_time,
        )

    # Add fallback compounds if missing
    for fallback in ["SOFT", "MEDIUM", "HARD"]:
        if fallback not in tyre_models:
            tyre_models[fallback] = TyreModel(
                compound=fallback,
                alpha=COMPOUND_ALPHA.get(fallback, 0.35),
                k=ref_k * COMPOUND_K_SCALE.get(fallback, 1.0),
                e=ref_e,
                base_lap_time=cal.base_lap_time,
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


def build_engine(
    track_name: str,
    seed: int,
    track_physics: Optional[TrackPhysics] = None,
) -> RaceEngine:
    """Construct the RaceEngine for one match. The only sanctioned way.

    `track_physics` is an override for the two callers that already hold a
    built TrackPhysics and must not rebuild it:

    - the sandboxed match child receives it prebuilt in its spec, because
      building it reads the calibration artifact from disk and the child is
      resource-limited (see match_job.run_match);
    - /api/test-bot shortens `total_laps` on the instance before handing it
      over, so a rebuild here would throw that away.

    When omitted, the physics come from the frozen calibration artifact.
    Everything else -- weather transitions, safety-car probabilities, the
    initial weather state -- is read from TRACKS and is never a parameter,
    so no caller can construct a race the others cannot reproduce.
    """
    config = TRACKS[track_name]
    if track_physics is None:
        track_physics = build_track_physics(track_name)
    return RaceEngine(
        track=track_physics,
        weather_transitions=config.weather_transitions,
        initial_weather="dry",
        seed=seed,
        sc_prob_dry=config.safety_car_prob_dry,
        sc_prob_wet=config.safety_car_prob_wet,
    )
