import json
import pytest
from dataclasses import dataclass
from backend.determinism.canonical import canonical_json
from backend.determinism.manifest import (
    MatchManifest, Participant, build_manifest, manifest_sha256,
)


def _participants():
    # Deliberately out of slot order (slot 1 before slot 0). A fixture built
    # already in ascending order can't tell "build_manifest sorted this"
    # apart from "this was merely passed through untouched" -- see
    # test_participants_keep_their_slot_order below, which depends on this.
    return [
        Participant(slot=1, player_id=None, bot_version_id=None,
                    code_sha256=None, house_bot="VEL-01"),
        Participant(slot=0, player_id="p_1", bot_version_id="bv_1",
                    code_sha256="sha256:" + "a" * 64, house_bot=None),
    ]


def _manifest():
    return build_manifest(
        match_id="m_01J", seed=1234567890, track="bahrain",
        participants=_participants(),
    )


def test_canonical_json_sorts_keys_and_omits_whitespace():
    assert canonical_json({"b": 1, "a": 2}) == b'{"a":2,"b":1}'


def test_canonical_json_is_stable_across_dict_insertion_order():
    assert canonical_json({"a": 1, "b": 2}) == canonical_json({"b": 2, "a": 1})


def test_manifest_carries_every_field_the_spec_requires():
    payload = json.loads(canonical_json(_manifest()))
    for field in ("match_id", "seed", "engine_version", "ruleset_version",
                  "calibration_id", "track", "python_version",
                  "dep_lock_sha256", "participants"):
        assert field in payload, f"manifest is missing {field}"


def test_participants_keep_their_slot_order():
    payload = json.loads(canonical_json(_manifest()))
    assert [p["slot"] for p in payload["participants"]] == [0, 1]


def test_hash_is_stable_and_content_addressed():
    assert manifest_sha256(_manifest()) == manifest_sha256(_manifest())


def test_changing_the_seed_changes_the_hash():
    other = build_manifest(match_id="m_01J", seed=999, track="bahrain",
                           participants=_manifest().participants)
    assert manifest_sha256(other) != manifest_sha256(_manifest())


def test_round_trips_through_json_unchanged():
    m = _manifest()
    assert canonical_json(MatchManifest.from_dict(json.loads(canonical_json(m)))) == canonical_json(m)


def test_duplicate_slots_are_rejected():
    """Two participants in one slot would share an RNG stream -- a silent
    determinism break, so build_manifest must refuse it rather than let it
    through to look like a normal (if unlucky) match."""
    with pytest.raises(ValueError):
        build_manifest(
            match_id="m_01J", seed=1, track="bahrain",
            participants=[
                Participant(slot=0, player_id="p_1", bot_version_id="bv_1",
                            code_sha256="sha256:" + "a" * 64, house_bot=None),
                Participant(slot=0, player_id="p_2", bot_version_id="bv_2",
                            code_sha256="sha256:" + "b" * 64, house_bot=None),
            ],
        )


def test_canonical_json_does_not_unwrap_a_nested_dataclass():
    """canonical_json only unwraps a dataclass that is the literal top-level
    argument (see the module docstring). A dataclass nested inside a dict or
    list is not converted and raises TypeError, same as json.dumps would for
    any other non-serializable object. This is a pinned contract, not an
    oversight: Task 7 already relies on it by calling asdict() itself before
    nesting a dataclass value into a larger payload."""
    @dataclass
    class Inner:
        x: int

    with pytest.raises(TypeError):
        canonical_json({"inner": Inner(x=1)})
