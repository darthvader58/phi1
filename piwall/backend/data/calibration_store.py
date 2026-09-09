"""Loads frozen calibration artifacts. Never imports numpy or scipy.

calibrate_track() fits with scipy.optimize.curve_fit, whose results shift
across BLAS/LAPACK backends. Fitting at race time therefore made every lap
time a function of the installed dependency versions. The fit now happens
offline in scripts/build_calibration.py; this module only reads its output.
"""

import hashlib
import json
from pathlib import Path
from typing import Dict

from .calibration_types import TrackCalibration, TyreDegParams

ARTIFACT_DIR = Path(__file__).resolve().parent.parent.parent / "calibration"


class CalibrationIntegrityError(ValueError):
    """An artifact's content no longer hashes to the digest in its filename.

    A hand-edited JSON, a bad merge, or a truncated write would otherwise
    load silently under a stale content address — the exact silent
    divergence this task exists to close.
    """


def _artifact_path(track: str) -> Path:
    matches = sorted(ARTIFACT_DIR.glob(f"{track}.sha256-*.json"))
    if not matches:
        raise FileNotFoundError(
            f"No committed calibration for {track!r}. "
            f"Run: python scripts/build_calibration.py {track}"
        )
    if len(matches) > 1:
        # Two artifacts for one track means an ambiguous history: a match
        # replayed against the wrong one would diverge silently.
        raise RuntimeError(f"Multiple calibrations for {track!r}: {[m.name for m in matches]}")
    return matches[0]


def _digest_from_filename(path: Path) -> str:
    return path.name.split(".sha256-")[1][:-5]


def calibration_id(track: str) -> str:
    """Content address of the artifact, recorded in the match manifest."""
    return "sha256:" + _digest_from_filename(_artifact_path(track))


def load_calibration(track: str) -> TrackCalibration:
    path = _artifact_path(track)
    raw = json.loads(path.read_text())
    compounds: Dict[str, TyreDegParams] = {
        name: TyreDegParams(**params) for name, params in sorted(raw["compounds"].items())
    }
    cal = TrackCalibration(
        track=raw["track"],
        base_lap_time=raw["base_lap_time"],
        pit_loss_seconds=raw["pit_loss_seconds"],
        compounds=compounds,
    )

    # The filename is a claim, not a fact: verify the bytes still hash to
    # the digest they're named after before handing the calibration out.
    expected = _digest_from_filename(path)
    actual = hashlib.sha256(canonical_calibration_bytes(cal)).hexdigest()
    if actual != expected:
        raise CalibrationIntegrityError(
            f"{path.name}: content hashes to sha256-{actual}, "
            f"but the filename claims sha256-{expected}"
        )

    return cal


def canonical_calibration_bytes(cal: TrackCalibration) -> bytes:
    """Byte form the calibration_id is computed over. Shared with the builder."""
    from dataclasses import asdict
    payload = {
        "track": cal.track,
        "base_lap_time": cal.base_lap_time,
        "pit_loss_seconds": cal.pit_loss_seconds,
        "compounds": {name: asdict(p) for name, p in sorted(cal.compounds.items())},
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
