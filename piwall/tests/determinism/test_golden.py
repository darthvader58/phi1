"""The Phase 1 gate: the same manifest must produce byte-identical replays."""

import hashlib
import json
from pathlib import Path

import pytest

from backend.determinism.manifest import MatchManifest
from backend.determinism.replay import replay_from_manifest

GOLDEN = Path(__file__).parent / "golden"
MANIFESTS = sorted(GOLDEN.glob("*.manifest.json"))

# One committed manifest per track (spec: bahrain, monaco, monza, silverstone,
# spa, suzuka). Pinned as a literal count, not derived from anything else in
# this file, so a partial deletion of the corpus is caught even though it
# would otherwise leave every remaining parametrized test green.
EXPECTED_TRACK_COUNT = 6


def _load(path):
    return MatchManifest.from_dict(json.loads(path.read_text()))


def test_golden_manifests_exist():
    assert MANIFESTS, "no golden manifests committed"


def test_golden_corpus_has_the_expected_number_of_tracks():
    """Guards against a *partial* deletion, which `test_golden_manifests_exist`
    cannot see.

    That check only fires if every manifest vanishes. Deleting five of six
    would silently narrow the gate from six tracks to one while every
    parametrized test below stays green -- and different tracks catch
    different mutations (a flipped DNF roll only reddens the one track whose
    corpus happens to hit that branch), so a narrowed corpus is a real loss
    of detection power, not just a cosmetic gap.
    """
    assert len(MANIFESTS) == EXPECTED_TRACK_COUNT, (
        f"expected {EXPECTED_TRACK_COUNT} golden manifests, found "
        f"{len(MANIFESTS)}: {[p.stem for p in MANIFESTS]}"
    )


def test_every_manifest_has_a_matching_expected_hash_file():
    """Neither a manifest missing its `.expected.txt` nor an orphaned
    `.expected.txt` with no manifest is otherwise noticed: the parametrized
    hash test only iterates MANIFESTS, so a stray expected-hash file is
    invisible to it, and a missing one would surface as a raw
    FileNotFoundError deep inside that test rather than a clear assertion
    here.
    """
    manifest_tracks = {p.name.removesuffix(".manifest.json") for p in MANIFESTS}
    expected_tracks = {p.name.removesuffix(".expected.txt") for p in GOLDEN.glob("*.expected.txt")}
    assert manifest_tracks == expected_tracks, (
        "manifest/expected-hash pairing is broken -- "
        f"manifests without a hash file: {sorted(manifest_tracks - expected_tracks)}, "
        f"hash files without a manifest: {sorted(expected_tracks - manifest_tracks)}"
    )


@pytest.mark.parametrize("path", MANIFESTS, ids=lambda p: p.stem)
def test_replay_matches_the_committed_hash(path):
    expected = path.with_name(path.name.replace(".manifest.json", ".expected.txt")).read_text().strip()
    actual = "sha256:" + hashlib.sha256(replay_from_manifest(_load(path))).hexdigest()
    assert actual == expected, (
        "Replay diverged from its committed hash. Either a change altered "
        "simulation output, or the determinism contract broke. Do not re-record "
        "without establishing which."
    )


@pytest.mark.parametrize("path", MANIFESTS, ids=lambda p: p.stem)
def test_one_hundred_runs_are_byte_identical(path):
    """The phase gate itself (spec 11).

    Runs across all six tracks, not just one: the corpus's only DNF is on
    suzuka, so a bahrain-only loop would never exercise a mid-race
    retirement -- a path that mutates self.cars and changes which car draws
    which number from the shared RNG stream next. That is exactly where a
    nondeterminism bug would hide, and the cost of covering it (roughly
    seven seconds total across all six tracks, versus one second for
    bahrain alone) is negligible against the full suite.
    """
    manifest = _load(path)
    first = replay_from_manifest(manifest)
    for run in range(99):
        assert replay_from_manifest(manifest) == first, f"diverged on run {run + 2}"
