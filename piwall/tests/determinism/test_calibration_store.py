import hashlib
import json
import re
import pytest
from backend.data.calibration_store import (
    ARTIFACT_DIR, CalibrationIntegrityError, calibration_id,
    canonical_calibration_bytes, load_calibration,
)

TRACKS = ["bahrain", "monaco", "monza", "spa", "silverstone", "suzuka"]


@pytest.mark.parametrize("track", TRACKS)
def test_every_track_has_a_committed_artifact(track):
    assert list(ARTIFACT_DIR.glob(f"{track}.sha256-*.json")), f"no artifact for {track}"


@pytest.mark.parametrize("track", TRACKS)
def test_calibration_id_is_a_prefixed_sha256(track):
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", calibration_id(track))


@pytest.mark.parametrize("track", TRACKS)
def test_loading_twice_gives_equal_calibration(track):
    assert load_calibration(track) == load_calibration(track)


@pytest.mark.parametrize("track", TRACKS)
def test_loaded_content_hashes_to_its_own_calibration_id(track):
    """The filename is a claim; this checks the bytes actually back it up."""
    cal = load_calibration(track)
    digest = hashlib.sha256(canonical_calibration_bytes(cal)).hexdigest()
    assert f"sha256:{digest}" == calibration_id(track)


def test_content_mismatch_raises_rather_than_loading_silently(tmp_path, monkeypatch):
    """A hand-edited or truncated artifact must not load under a stale address."""
    from backend.data import calibration_store

    real_path = next(iter(ARTIFACT_DIR.glob("bahrain.sha256-*.json")))
    raw = json.loads(real_path.read_text())
    raw["base_lap_time"] += 1.0  # corrupt content; filename keeps the old digest

    corrupted = tmp_path / real_path.name
    corrupted.write_text(json.dumps(raw))
    monkeypatch.setattr(calibration_store, "ARTIFACT_DIR", tmp_path)

    with pytest.raises(CalibrationIntegrityError):
        load_calibration("bahrain")


def test_loading_does_not_import_scipy():
    """The runner image has no scipy; an accidental import would crash it."""
    import subprocess, sys
    from pathlib import Path
    # Derived, not hardcoded: this same suite runs inside the container,
    # where the tree lives at /app rather than at any developer's path.
    repo = Path(__file__).resolve().parents[2]
    code = (
        "import sys; from backend.data.calibration_store import load_calibration; "
        "load_calibration('bahrain'); "
        "assert 'scipy' not in sys.modules, 'scipy was imported'"
    )
    subprocess.run([sys.executable, "-c", code], check=True, cwd=repo)


def test_unknown_track_raises_rather_than_silently_calibrating():
    with pytest.raises(FileNotFoundError):
        load_calibration("nurburgring")
