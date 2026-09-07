import random
from backend.determinism.manifest import MatchManifest, Participant, build_manifest
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


def _manifest_direct(participants, seed=42, track="bahrain"):
    """Construct a MatchManifest directly, bypassing build_manifest's slot sort.

    build_manifest always returns participants pre-sorted by slot, so no
    manifest built the normal way can ever exercise replay_from_manifest's
    own sort. This is the only way to reach that branch.
    """
    return MatchManifest(
        match_id="m_direct",
        seed=seed,
        engine_version="test",
        ruleset_version="test",
        calibration_id="test",
        track=track,
        python_version="test",
        dep_lock_sha256="test",
        participants=participants,
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


def test_replay_from_manifest_is_unaffected_by_participant_list_order():
    """replay_from_manifest must re-sort by slot itself, not trust its input.

    build_manifest always hands it a pre-sorted list, so every manifest
    produced the normal way would pass even if the internal sort were
    deleted -- that's exactly the gap this test closes, by feeding the same
    two participants in two different list orders and requiring an identical
    *simulated outcome*. List order (not just the slot values, which are set
    explicitly per participant regardless of iteration order) drives the
    order cars are registered with the engine, which drives the order they
    draw from the shared per-race RNG stream during simulation -- so a
    missing sort would make this fail, even though every self-consistency
    test above would still pass.

    The comparison excludes the "manifest" key: the manifest is embedded
    verbatim (deliberately -- see the module docstring), so it legitimately
    preserves whatever participant list order the caller passed in. That is
    not the property under test here; the race outcome derived from it is.
    """
    import json

    forward = [
        Participant(0, None, None, None, "VEL-01"),
        Participant(1, None, None, None, "NXS-07"),
    ]
    backward = [
        Participant(1, None, None, None, "NXS-07"),
        Participant(0, None, None, None, "VEL-01"),
    ]
    payload_forward = json.loads(replay_from_manifest(_manifest_direct(forward)))
    payload_backward = json.loads(replay_from_manifest(_manifest_direct(backward)))
    del payload_forward["manifest"]
    del payload_backward["manifest"]
    assert payload_forward == payload_backward


def test_slot_order_maps_to_grid_position(monkeypatch):
    """Ground truth for slot -> starting grid position: slot 0 is grid
    position 1, slot 1 is position 2, slot 2 is position 3 -- this specific
    mapping, not merely "the same mapping on every run" (self-consistency),
    which a reversed or otherwise permuted mapping would satisfy just as
    well on every test above.
    """
    from backend.engine.race import RaceEngine

    calls = []
    original_add_car = RaceEngine.add_car

    def recording_add_car(self, car_id, player_id, strategy, starting_position, **kwargs):
        calls.append((car_id, starting_position))
        return original_add_car(self, car_id, player_id, strategy, starting_position, **kwargs)

    monkeypatch.setattr(RaceEngine, "add_car", recording_add_car)

    manifest = _manifest_direct([
        Participant(2, None, None, None, "WXP-23"),
        Participant(0, None, None, None, "VEL-01"),
        Participant(1, None, None, None, "NXS-07"),
    ])
    replay_from_manifest(manifest)

    assert dict(calls) == {"VEL-01": 1, "NXS-07": 2, "WXP-23": 3}
