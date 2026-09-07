import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from backend.sandbox.isolation import ChildFailed, LimitExceeded, MatchAborted
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


def _spec_with(code):
    spec = make_spec()
    spec["cars"] = [
        {"car_id": "c1", "player_id": "p1", "code": code,
         "start_position": 1, "starting_compound": "MEDIUM"},
        spec["cars"][1],
    ]
    return spec


def _spec_with_a_hanging_bot():
    return _spec_with(HANGS)


def test_a_hanging_bot_forfeits_its_decisions_and_the_match_completes():
    """A bot that never returns must not hang the server.

    The cut-off is now the counted-operation budget, not a SIGALRM: the
    hanging bot spends DEFAULT_DECISION_OPS executed lines and forfeits, on
    the same lap and after the same number of lines on every machine. The
    race finishes normally, which is the better outcome -- one player's bad
    bot does not deny everyone else their race -- and the forfeit is recorded
    as a "budget_forfeit" event so the replay shows what happened.

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

    forfeits = [e for e in result["events"]
                if e["event_type"] == "budget_forfeit"]
    assert forfeits and all(e["car_id"] == "c1" for e in forfeits)


# The counter cannot bound this: each line is one trace event but does
# unbounded work inside C, so the whole budget would cost minutes. This is
# the shape the resource limits still exist for.
BURNS_CPU_PER_LINE = (
    "def my_strategy(state, my_car):\n"
    "    while True:\n"
    "        n = len(sorted(range(400000)))\n"
)


def test_a_bot_the_counter_cannot_bound_aborts_the_match():
    """The abort path is still armed where the op budget cannot reach.

    A bot whose cost hides inside C calls spends few operations and much
    time, so the counted budget never trips. Elapsed time must not decide a
    recorded race, so the outcome here is an abort -- the match is voided,
    not completed with a machine-speed-dependent forfeit written into it.

    Both limits are set low: the bot burns CPU (tripping RLIMIT_CPU) and wall
    time (tripping the parent's poll, or runner.py's own wall-clock net).
    Any of them is enough, and they fail in opposite directions under load,
    so this asserts the guarantee rather than one mechanism.
    """
    with pytest.raises(LimitExceeded):
        run_match_isolated(_spec_with(BURNS_CPU_PER_LINE),
                           cpu_seconds=30, wall_seconds=30)


# Everything this bot does happens inside its own try, so the exception the
# wall-clock net raises lands where a bare `except:` catches it. Raising at a
# bot is only a request to stop; this is the bot declining.
SWALLOWS_THE_NET = (
    "def my_strategy(state, my_car):\n"
    "    x = 0\n"
    "    while True:\n"
    "        try:\n"
    "            while x < 1000000000:\n"
    "                x = x + 1\n"
    "        except:\n"
    "            x = 0\n"
)


def test_a_bot_that_refuses_the_wall_clock_signal_is_still_stopped():
    """No in-process exception can bound a loop that catches everything.

    A single alarm let this run for 45s against an 800ms net. The alarm now
    repeats, and a bot still running an interval after being told to stop is
    exited outright from inside the isolated child -- the one door a bare
    `except:` cannot cover. It is classified as the bot's limit breach, not
    as a crash of ours.
    """
    started = time.monotonic()
    with pytest.raises(LimitExceeded):
        run_match_isolated(_spec_with(SWALLOWS_THE_NET),
                           cpu_seconds=30, wall_seconds=30)
    # Bounded by our own net at a small multiple of the per-decision limit,
    # not by RLIMIT_CPU seconds later.
    assert time.monotonic() - started < 20


def test_a_voided_match_is_not_reported_as_an_engine_bug():
    """LimitExceeded and ChildFailed are different channels on purpose.

    ChildFailed means our code broke and shows the player a generic message;
    LimitExceeded means their bot did something and names it. A wall-clock
    void crossing the process boundary used to arrive as an unrecognised
    type name and be reported as the former.
    """
    with pytest.raises(LimitExceeded) as excinfo:
        run_match_isolated(_spec_with(BURNS_CPU_PER_LINE),
                           cpu_seconds=30, wall_seconds=30)
    assert not isinstance(excinfo.value, ChildFailed)
    assert "wall-clock" in str(excinfo.value)


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
