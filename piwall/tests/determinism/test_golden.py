"""The Phase 1 gate: the same manifest must produce byte-identical replays."""

import hashlib
import json
from pathlib import Path

import pytest

from backend.determinism.manifest import MatchManifest
from backend.determinism.replay import replay_from_manifest

GOLDEN = Path(__file__).parent / "golden"
MANIFESTS = sorted(GOLDEN.glob("*.manifest.json"))


def _load(path):
    return MatchManifest.from_dict(json.loads(path.read_text()))


def test_golden_manifests_exist():
    assert MANIFESTS, "no golden manifests committed"


@pytest.mark.parametrize("path", MANIFESTS, ids=lambda p: p.stem)
def test_replay_matches_the_committed_hash(path):
    expected = path.with_name(path.name.replace(".manifest.json", ".expected.txt")).read_text().strip()
    actual = "sha256:" + hashlib.sha256(replay_from_manifest(_load(path))).hexdigest()
    assert actual == expected, (
        "Replay diverged from its committed hash. Either a change altered "
        "simulation output, or the determinism contract broke. Do not re-record "
        "without establishing which."
    )


@pytest.mark.parametrize("path", MANIFESTS[:1], ids=lambda p: p.stem)
def test_one_hundred_runs_are_byte_identical(path):
    """The phase gate itself (spec 11)."""
    manifest = _load(path)
    first = replay_from_manifest(manifest)
    for run in range(99):
        assert replay_from_manifest(manifest) == first, f"diverged on run {run + 2}"
