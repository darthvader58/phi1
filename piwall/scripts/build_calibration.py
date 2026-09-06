"""Offline calibration build. The only place scipy runs.

Usage: python scripts/build_calibration.py [track ...]

Writes calibration/<track>.sha256-<id>.json. Commit the result: a match
manifest records the calibration_id, so recalibrating creates a new artifact
rather than invalidating replay history. Delete the old file only once no
manifest references it.
"""

import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.data.calibration import calibrate_track
from backend.data.calibration_store import ARTIFACT_DIR, canonical_calibration_bytes
from backend.data.tracks import TRACKS


def build(track: str) -> Path:
    cal = calibrate_track(track)
    payload = canonical_calibration_bytes(cal)
    digest = hashlib.sha256(payload).hexdigest()
    ARTIFACT_DIR.mkdir(exist_ok=True)
    out = ARTIFACT_DIR / f"{track}.sha256-{digest}.json"
    out.write_bytes(payload)
    print(f"{track}: {out.name}")
    return out


if __name__ == "__main__":
    targets = sys.argv[1:] or sorted(TRACKS)
    for name in targets:
        build(name)
