import os
import subprocess
import sys
import time
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


def _spec_with_a_hanging_bot():
    spec = make_spec()
    spec["cars"] = [
        {"car_id": "c1", "player_id": "p1", "code": HANGS,
         "start_position": 1, "starting_compound": "MEDIUM"},
        spec["cars"][1],
    ]
    return spec


def test_a_hanging_bot_forfeits_its_decisions_and_the_match_completes():
    """A bot that never returns must not hang the server.

    Moving execution into a child process revived runner.py's 50ms SIGALRM
    guard, which is dead in the API process because signal.signal() cannot arm
    off the main thread -- inside the child, the engine runs on the child's
    main thread, so it arms. The hanging bot therefore forfeits each decision
    (~50ms apiece) rather than blocking, and the race finishes normally. That
    is the better outcome: one player's bad bot does not deny everyone else
    their race.

    Limits are deliberately generous so this asserts the forfeit behaviour and
    not a race against a limit.
    """
    started = time.monotonic()
    result = run_match_isolated(
        _spec_with_a_hanging_bot(), cpu_seconds=30, wall_seconds=60,
    )
    elapsed = time.monotonic() - started

    # Returning at all is the guarantee: run_isolated would have raised
    # MatchAborted rather than let the call block past its wall budget.
    assert elapsed < 60
    assert len(result["standings"]) == 2
    assert result["lap_data"]

    hanging = [c for c in result["standings"] if c["car_id"] == "c1"][0]
    # Every decision it attempted was cut off, so it never pitted.
    assert hanging["pit_count"] == 0


def test_a_bot_that_exhausts_the_childs_budget_aborts_the_match():
    """The abort path is still armed for a bot the 50ms guard cannot absorb.

    Both limits are set low: the forfeits burn CPU (tripping RLIMIT_CPU) and
    wall time (tripping the parent's poll). Either alone is enough, and they
    fail in opposite directions under load -- SIGALRM is ITIMER_REAL, i.e.
    wall-clock, so contention makes each forfeit burn *less* CPU but *more*
    wall time. Asserting the guarantee rather than one mechanism is what keeps
    this stable; pinning it to CPU alone is what made the previous version of
    this test flaky.
    """
    with pytest.raises(MatchAborted):
        run_match_isolated(
            _spec_with_a_hanging_bot(), cpu_seconds=1, wall_seconds=5,
        )


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
