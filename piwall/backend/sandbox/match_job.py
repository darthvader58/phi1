"""Picklable match entry point executed inside an isolated child process.

Engine construction lives here rather than in main.py so the child can rebuild
the whole match from a plain dict, with no reference to server state.
"""

from typing import Any, Dict

from backend.determinism.budget import DEFAULT_DECISION_OPS
from backend.engine.bots import BUILTIN_BOTS
from backend.engine.race import Decision, RaceEngine
from backend.sandbox.isolation import (
    DEFAULT_CPU_SECONDS,
    DEFAULT_MEMORY_MB,
    DEFAULT_WALL_SECONDS,
    run_isolated,
)
from backend.engine.serialize import car_state_to_dict, race_state_to_dict
from backend.sandbox.runner import DEFAULT_DECISION_WALL_MS, execute_strategy


def _make_user_strategy(code: str, seed: int, slot: int,
                        max_ops: int = DEFAULT_DECISION_OPS,
                        timeout_ms: int = DEFAULT_DECISION_WALL_MS):
    def strategy(state, my_car):
        # BudgetForfeit and DecisionTimeout deliberately travel straight
        # through: only RaceEngine knows the lap this happened on, and only
        # it can record the forfeit as a replay event.
        result = execute_strategy(code, race_state_to_dict(state),
                                  car_state_to_dict(my_car), timeout_ms,
                                  seed=seed, slot=slot, max_ops=max_ops)
        if "error" in result:
            return Decision(pit=False, compound=my_car.compound)
        return Decision(pit=result["pit"], compound=result["compound"])

    return strategy


def run_match(spec: Dict[str, Any]) -> Dict[str, Any]:
    """Run one full match. Module-level and picklable for the spawn context.

    Track physics arrive prebuilt in the spec. The child never calls
    build_track_physics: that runs a scipy curve fit and writes
    processed_cache/calibration_<track>.json, and the child sets
    RLIMIT_FSIZE=0, so the write fails with OSError. Calibrating outside the
    sandbox is also correct on its own terms -- it keeps an expensive fit off
    the per-match path and is the seam where Phase 1 swaps in a frozen
    calibration artifact (spec 5.3).
    """
    track = spec["track_physics"]
    config = spec["track_config"]

    engine = RaceEngine(
        track=track,
        weather_transitions=config.weather_transitions,
        seed=spec["seed"],
        sc_prob_dry=config.safety_car_prob_dry,
        sc_prob_wet=config.safety_car_prob_wet,
    )

    for slot, car in enumerate(spec["cars"]):
        if car.get("code"):
            strategy = _make_user_strategy(car["code"], spec["seed"], slot)
        else:
            strategy = BUILTIN_BOTS[car["bot_id"]]["strategy"]
        engine.add_car(
            car["car_id"], car["player_id"], strategy,
            car["start_position"], car.get("starting_compound", "MEDIUM"),
        )

    result = engine.run()
    return {
        "track": result.track,
        "total_laps": result.total_laps,
        "standings": [car_state_to_dict(c) for c in result.final_standings],
        "events": [
            {"lap": e.lap, "event_type": e.event_type,
             "car_id": e.car_id, "detail": e.detail}
            for e in result.events
        ],
        "lap_data": result.lap_data,
        "weather_history": result.weather_history,
    }


def run_match_isolated(
    spec: Dict[str, Any],
    memory_mb: int = DEFAULT_MEMORY_MB,
    cpu_seconds: int = DEFAULT_CPU_SECONDS,
    wall_seconds: int = DEFAULT_WALL_SECONDS,
) -> Dict[str, Any]:
    """Run a match in a resource-limited child process."""
    return run_isolated(
        run_match, (spec,),
        memory_mb=memory_mb, cpu_seconds=cpu_seconds, wall_seconds=wall_seconds,
    )
