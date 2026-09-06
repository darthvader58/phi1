"""Offline calibration build. The only place scipy runs.

Usage: python scripts/build_calibration.py [track ...]

Writes calibration/<track>.sha256-<id>.json. load_calibration() takes no
calibration id: it loads whatever single artifact exists for a track, and
raises RuntimeError if it finds more than one. So only one artifact per
track can exist at a time — recalibrating replaces it, not adds to it.
Delete the old file in the same commit that adds the new one, or every load
for that track (including live races) starts raising. Selecting a specific
historical calibration by id is not supported today; that is future work
for whatever later phase teaches load_calibration to take one.
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
