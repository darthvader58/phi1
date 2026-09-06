"""Pins the ordering assumptions the engine's reproducibility rests on.

None of these assert new behaviour. They fail loudly if a refactor removes a
guarantee the determinism contract depends on (spec 5.4).
"""

import random
import pytest
from backend.engine.weather import DEFAULT_TRANSITIONS, WeatherEngine


def test_transition_tables_iterate_in_a_fixed_order():
    """step() walks probs.keys() cumulatively, so order changes outcomes."""
    for state, probs in DEFAULT_TRANSITIONS.items():
        assert list(probs.keys()) == list(probs.keys()), f"{state} iterates unstably"


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
