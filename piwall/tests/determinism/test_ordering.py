"""Pins the ordering assumptions the engine's reproducibility rests on.

None of these assert new behaviour. They fail loudly if a refactor removes a
guarantee the determinism contract depends on (spec 5.4).
"""

import random

import pytest

from backend.data.tracks import TRACKS
from backend.engine.weather import DEFAULT_TRANSITIONS, WeatherEngine

# The order WeatherEngine.step() walks each row, cumulating probabilities
# against one RNG draw. Written out as a literal rather than derived from
# any table, so a reordered table is a failure and not a moving target.
EXPECTED_ORDER = {
    "dry": ["dry", "damp", "wet"],
    "damp": ["dry", "damp", "wet"],
    "wet": ["dry", "damp", "wet"],
    "drying": ["dry", "damp", "wet", "drying"],
}


def _assert_rows_iterate_in_the_pinned_order(transitions, label):
    assert set(transitions) == set(EXPECTED_ORDER), f"{label}: state set changed"
    for state, probs in transitions.items():
        assert list(probs.keys()) == EXPECTED_ORDER[state], (
            f"{label}: the {state!r} row iterates in a different order "
            f"({list(probs.keys())}). step() cumulates probabilities in key "
            f"order against a single RNG draw, so reordering a row changes "
            f"which weather that draw selects -- every replay of every "
            f"existing match then diverges."
        )


@pytest.mark.parametrize("track", sorted(TRACKS), ids=str)
def test_per_track_transition_tables_iterate_in_a_fixed_order(track):
    """The tables the determinism contract actually rests on (spec 5.4).

    Every race is built from TRACKS[track].weather_transitions;
    DEFAULT_TRANSITIONS is only the fallback for a track that defines none,
    and all six define their own. Pinning the fallback alone left the real
    tables free to be reordered -- the exact refactor this file exists to
    catch -- with the whole suite still green.
    """
    _assert_rows_iterate_in_the_pinned_order(
        TRACKS[track].weather_transitions, f"tracks.py:{track}"
    )


def test_every_track_defines_its_own_transition_table():
    """Otherwise the test above passes vacuously on an empty dict while the
    track silently races on DEFAULT_TRANSITIONS."""
    missing = sorted(t for t, cfg in TRACKS.items() if not cfg.weather_transitions)
    assert not missing, f"tracks with no weather_transitions of their own: {missing}"


def test_transition_tables_iterate_in_a_fixed_order():
    """The fallback table, still reachable by any track that defines none."""
    _assert_rows_iterate_in_the_pinned_order(DEFAULT_TRANSITIONS, "DEFAULT_TRANSITIONS")


def test_weather_is_reproducible_from_a_seed():
    def run():
        model = WeatherEngine(rng=random.Random(7))
        return [model.step() for _ in range(60)]
    assert run() == run()


def test_weather_sequences_differ_across_seeds():
    def run(seed):
        model = WeatherEngine(rng=random.Random(seed))
        return [model.step() for _ in range(60)]
    assert run(7) != run(8)


def test_engine_hot_path_does_not_import_numpy():
    """numpy's float paths vary with the BLAS backend (spec 5.4)."""
    import subprocess, sys
    from pathlib import Path
    repo = Path(__file__).resolve().parents[2]
    code = (
        "import sys; import backend.engine.race, backend.engine.physics, "
        "backend.engine.weather; "
        "assert 'numpy' not in sys.modules, 'numpy reached the hot path'"
    )
    subprocess.run([sys.executable, "-c", code], check=True, cwd=repo)
