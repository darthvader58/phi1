import pytest

from backend.sandbox.isolation import MatchAborted
from backend.sandbox.match_job import run_match_isolated


def make_spec():
    """Build a match spec. Track physics are built HERE, in the parent, because
    build_track_physics writes a calibration cache file and the isolated child
    forbids file writes (RLIMIT_FSIZE=0)."""
    from backend.data.tracks import TRACKS
    from backend.engine.cli_runner import build_track_physics

    return {
        "track": "bahrain",
        "track_physics": build_track_physics("bahrain"),
        "track_config": TRACKS["bahrain"],
        "seed": 42,
        "cars": [
            {"car_id": "c1", "player_id": "p1", "bot_id": "VEL-01",
             "start_position": 1, "starting_compound": "MEDIUM"},
            {"car_id": "c2", "player_id": "p2", "bot_id": "NXS-07",
             "start_position": 2, "starting_compound": "MEDIUM"},
        ],
    }


HANGS = (
    "def my_strategy(state, my_car):\n"
    "    while True:\n"
    "        pass\n"
)


def test_runs_a_match_and_returns_standings():
    result = run_match_isolated(make_spec())
    assert len(result["standings"]) == 2
    assert result["lap_data"]


def test_a_hanging_bot_cannot_hang_the_server():
    spec = make_spec()
    spec["cars"] = [
        {"car_id": "c1", "player_id": "p1", "code": HANGS,
         "start_position": 1, "starting_compound": "MEDIUM"},
        spec["cars"][1],
    ]
    with pytest.raises(MatchAborted):
        run_match_isolated(spec, cpu_seconds=3, wall_seconds=10)
