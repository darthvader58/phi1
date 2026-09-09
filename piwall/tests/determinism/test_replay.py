import random

import pytest

from backend.data.calibration_store import calibration_id
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

    calibration_id is the real one for the track, not a placeholder:
    replay_from_manifest refuses to replay against a calibration artifact
    the manifest was not built against.
    """
    return MatchManifest(
        match_id="m_direct",
        seed=seed,
        engine_version="test",
        ruleset_version="test",
        calibration_id=calibration_id(track),
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


# ─── The replay must simulate the race the game actually runs ─────────

def test_replay_gets_the_tracks_own_weather_and_safety_car_parameters(monkeypatch):
    """replay_from_manifest used to build its RaceEngine with neither
    `weather_transitions` nor the per-track safety-car probabilities, so
    every replay silently fell back to weather.DEFAULT_TRANSITIONS and to
    the engine's own default SC odds while the production match job used the
    track's. Nothing caught it: the six golden tracks differ only in
    calibration and lap count, so a replay racing through weather the game
    never produces still hashed consistently with itself.
    """
    from backend.data.tracks import TRACKS
    from backend.engine import build as build_module
    from backend.engine.weather import DEFAULT_TRANSITIONS

    captured = {}
    real_engine = build_module.RaceEngine

    class RecordingEngine(real_engine):
        def __init__(self, *args, **kwargs):
            captured.update(kwargs)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(build_module, "RaceEngine", RecordingEngine)
    replay_from_manifest(_manifest())

    config = TRACKS["bahrain"]
    assert captured["weather_transitions"] == config.weather_transitions
    assert captured["sc_prob_dry"] == config.safety_car_prob_dry
    assert captured["sc_prob_wet"] == config.safety_car_prob_wet
    # The bug's signature, stated directly: the fallback table is not the
    # track's, and a replay must never end up on it.
    assert captured["weather_transitions"] != DEFAULT_TRANSITIONS


def test_a_bahrain_replay_never_rains():
    """Bahrain is a desert: dry->dry is 0.998 in tracks.py. Under
    DEFAULT_TRANSITIONS (0.92) a 57-lap race is wet somewhere with
    probability ~0.99, so this is a behavioural check on which table
    reached the engine, not a restatement of the one above.
    """
    import json
    assert set(json.loads(_run())["weather_history"]) == {"dry"}


def test_replay_reproduces_the_production_match_path_exactly():
    """The contract in one assertion: a manifest replayed and the same match
    run through backend.sandbox.match_job -- the code path a real race takes
    -- must produce the same race. The two used to construct their engines
    differently, which no per-call-site test could see.
    """
    import json

    from backend.engine.bots import BUILTIN_BOTS
    from backend.engine.build import build_track_physics
    from backend.sandbox.match_job import run_match

    participants = [
        Participant(0, None, None, None, "VEL-01"),
        Participant(1, None, None, None, "NXS-07"),
        Participant(2, None, None, None, "WXP-23"),
    ]
    manifest = _manifest_direct(participants, seed=4242)
    replayed = json.loads(replay_from_manifest(manifest))

    produced = run_match({
        "track": manifest.track,
        "track_physics": build_track_physics(manifest.track),
        "seed": manifest.seed,
        "cars": [
            {"car_id": p.house_bot, "player_id": p.house_bot,
             "bot_id": p.house_bot, "start_position": p.slot + 1,
             "starting_compound": BUILTIN_BOTS[p.house_bot]["starting_compound"]}
            for p in participants
        ],
    })

    assert replayed["weather_history"] == produced["weather_history"]
    assert replayed["final_standings"] == produced["standings"]
    assert replayed["lap_data"] == produced["lap_data"]
    assert replayed["total_laps"] == produced["total_laps"]


# ─── The calibration the manifest names is the one that must be loaded ──

def test_replay_refuses_a_manifest_built_against_another_calibration():
    """build_engine loads whatever artifact is on disk. A replaced
    calibration would otherwise make every replay quietly wrong, and would
    surface as "the determinism contract broke" rather than "the wrong
    calibration is loaded".
    """
    from backend.determinism.replay import CalibrationMismatch

    manifest = _manifest_direct(
        [Participant(0, None, None, None, "VEL-01")],
    )
    manifest.calibration_id = "sha256:" + "0" * 64

    with pytest.raises(CalibrationMismatch) as excinfo:
        replay_from_manifest(manifest)
    message = str(excinfo.value)
    # Both values, so the error says what was expected *and* what was found.
    assert manifest.calibration_id in message
    assert calibration_id("bahrain") in message


def test_replay_accepts_the_calibration_the_manifest_names():
    """The guard must not be a blanket refusal: build_manifest records the
    artifact on disk, so the normal path stays green."""
    assert replay_from_manifest(_manifest())
