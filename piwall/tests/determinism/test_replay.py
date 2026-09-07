import random
from backend.determinism.manifest import Participant, build_manifest
from backend.determinism.replay import (
    REPLAY_FORMAT_VERSION, replay_from_manifest,
)


def _manifest(seed=42):
    return build_manifest(
        match_id="m_test", seed=seed, track="bahrain",
        participants=[
            Participant(0, None, None, None, "VEL-01"),
            Participant(1, None, None, None, "NXS-07"),
        ],
    )


def _run(seed=42):
    """Re-run through the manifest path, which is what the gate exercises."""
    return replay_from_manifest(_manifest(seed))


def test_replay_declares_its_format_version():
    import json
    assert json.loads(_run())["format_version"] == REPLAY_FORMAT_VERSION


def test_the_same_seed_produces_byte_identical_replays():
    assert _run(42) == _run(42)


def test_different_seeds_produce_different_replays():
    assert _run(42) != _run(43)


def test_the_replay_embeds_the_manifest_that_produced_it():
    import json
    payload = json.loads(_run())
    assert payload["manifest"]["seed"] == 42
    assert payload["manifest"]["track"] == "bahrain"


def test_a_global_random_call_between_runs_cannot_change_the_replay():
    """Catches any residual dependence on the process-global stream."""
    first = _run(42)
    for _ in range(1000):
        random.random()
    assert _run(42) == first
