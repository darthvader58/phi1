import os
import subprocess
import sys
from pathlib import Path

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


PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Imports the child's whole graph: match_job statically, plus data.tracks and
# engine.physics, which the child pulls in when it unpickles the spec's
# track_config and track_physics.
NUMPY_PROBE = """
import sys

assert "numpy" not in sys.modules, "numpy was already loaded before the probe"

import backend.sandbox.match_job
import backend.data.tracks
import backend.engine.physics

leaked = sorted(
    m for m in sys.modules
    if m.split(".")[0] in ("numpy", "scipy", "pandas", "fastf1")
)
if leaked:
    sys.exit("heavy modules reached the isolated child: " + ", ".join(leaked))
"""


def test_the_sandbox_child_does_not_import_numpy():
    """numpy must stay out of the isolated child's import graph.

    On Linux, RLIMIT_AS bounds *virtual* address space, and numpy's BLAS
    backend reserves well past the 512MB default -- a stray numpy import
    would abort every match in production. macOS rejects RLIMIT_AS outright,
    so no amount of running matches here can catch it; the import graph has
    to be asserted on directly.

    Run in a fresh interpreter because pytest has numpy loaded already.
    """
    proc = subprocess.run(
        [sys.executable, "-c", NUMPY_PROBE],
        cwd=str(PROJECT_ROOT),
        env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT)},
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
