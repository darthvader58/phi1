"""The immutable description of a match (spec 5.1)."""

import hashlib
import platform
from dataclasses import dataclass, field
from typing import List, Optional

from .canonical import canonical_json
from .lockfile import dep_lock_sha256
from ..data.calibration_store import calibration_id

ENGINE_VERSION = "2.1.0"
RULESET_VERSION = "2026.1"


@dataclass
class Participant:
    slot: int
    player_id: Optional[str]
    bot_version_id: Optional[str]
    code_sha256: Optional[str]
    house_bot: Optional[str]


@dataclass
class MatchManifest:
    match_id: str
    seed: int
    engine_version: str
    ruleset_version: str
    calibration_id: str
    track: str
    python_version: str
    dep_lock_sha256: str
    participants: List[Participant] = field(default_factory=list)

    @classmethod
    def from_dict(cls, raw: dict) -> "MatchManifest":
        return cls(
            **{k: v for k, v in raw.items() if k != "participants"},
            participants=[Participant(**p) for p in raw["participants"]],
        )


def build_manifest(match_id: str, seed: int, track: str,
                   participants: List[Participant]) -> MatchManifest:
    """Capture everything a replay needs, at the moment the match is created."""
    slots = [p.slot for p in participants]
    if len(slots) != len(set(slots)):
        # Two participants in one slot would share an RNG stream and a grid
        # position — a silent determinism break, not a style issue.
        raise ValueError(f"duplicate participant slots: {sorted(slots)}")
    return MatchManifest(
        match_id=match_id,
        seed=seed,
        engine_version=ENGINE_VERSION,
        ruleset_version=RULESET_VERSION,
        calibration_id=calibration_id(track),
        track=track,
        python_version=platform.python_version(),
        dep_lock_sha256=dep_lock_sha256(),
        # Sorted by slot: participant order decides RNG stream assignment, so
        # an unsorted list would silently change the match.
        participants=sorted(participants, key=lambda p: p.slot),
    )


def manifest_sha256(manifest: MatchManifest) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(manifest)).hexdigest()
