import re
import pytest
from backend.data.calibration_store import (
    ARTIFACT_DIR, calibration_id, load_calibration,
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
