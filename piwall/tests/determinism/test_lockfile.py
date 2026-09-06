import re
from backend.determinism.lockfile import REQUIREMENTS_PATH, dep_lock_sha256


def test_hash_is_a_prefixed_sha256():
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", dep_lock_sha256())


def test_hash_is_stable_across_calls():
    assert dep_lock_sha256() == dep_lock_sha256()


def test_every_runtime_requirement_is_exactly_pinned():
    """A floor pin lets a rebuild change float results underneath a replay."""
    lines = [
        line.strip()
        for line in REQUIREMENTS_PATH.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert lines, "requirements.txt is empty"
    unpinned = [line for line in lines if "==" not in line]
    assert unpinned == [], f"not exactly pinned: {unpinned}"


def test_numpy_and_scipy_are_not_runtime_dependencies():
    """They belong to the calibration tool only (spec 5.3)."""
    text = REQUIREMENTS_PATH.read_text().lower()
    assert "numpy" not in text
    assert "scipy" not in text
