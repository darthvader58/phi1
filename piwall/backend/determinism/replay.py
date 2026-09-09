"""Canonical replay artifact: the thing whose sha256 the contract is about.

The manifest is embedded rather than referenced so a replay is verifiable on
its own — re-running the embedded manifest must reproduce these exact bytes.
"""

import hashlib
from dataclasses import asdict

from .canonical import canonical_json
from .manifest import MatchManifest
from ..data.calibration_store import calibration_id
from ..engine.race import RaceResult
from ..engine.serialize import car_state_to_dict

REPLAY_FORMAT_VERSION = "1.0.0"


class CalibrationMismatch(ValueError):
    """The calibration on disk is not the one the manifest was built against.

    Every lap time comes out of the calibration artifact, so replaying
    against a different one produces a different race. Left unchecked that
    surfaces downstream as "the determinism contract broke" -- a moved
    golden hash with no explanation -- rather than as the far more useful
    "the wrong calibration is on disk".
    """


def replay_bytes(result: RaceResult, manifest: MatchManifest) -> bytes:
    payload = {
        "format_version": REPLAY_FORMAT_VERSION,
        "manifest": asdict(manifest),
        "track": result.track,
        "total_laps": result.total_laps,
        # List order is the finishing order and is load-bearing; canonical_json
        # sorts dict keys but never reorders a list.
        "final_standings": [car_state_to_dict(c) for c in result.final_standings],
        "events": [asdict(e) for e in result.events],
        "lap_data": result.lap_data,
        "weather_history": result.weather_history,
    }
    return canonical_json(payload)


def replay_sha256(result: RaceResult, manifest: MatchManifest) -> str:
    return "sha256:" + hashlib.sha256(replay_bytes(result, manifest)).hexdigest()


def replay_from_manifest(manifest: MatchManifest) -> bytes:
    """Re-run a match from its manifest alone. This *is* the contract.

    The grid is rebuilt from manifest.participants rather than from any
    ambient state, because anything the manifest does not name must not be
    able to influence the result. Slot order is load-bearing twice over: it
    sets the starting grid and it selects each bot's RNG stream.
    """
    # Imported here, not at module scope: engine.build pulls in the whole
    # simulation (physics, bots, track data), and replay.py is imported by
    # tests that only ever touch manifests. calibration_store is different --
    # manifest.py already imports it at module scope, so naming it at the top
    # of this file costs nothing.
    from ..engine.bots import BUILTIN_BOTS
    from ..engine.build import build_engine

    # The manifest names the calibration it was built against; build_engine
    # loads whatever artifact is on disk. Unchecked, a replaced artifact
    # would make every replay quietly wrong. This is not the same kind of
    # check as one on python_version would be: that describes the ambient
    # process, which a replay deliberately does not constrain, whereas the
    # calibration is a file this very function reads.
    on_disk = calibration_id(manifest.track)
    if on_disk != manifest.calibration_id:
        raise CalibrationMismatch(
            f"manifest {manifest.match_id!r} was built against calibration "
            f"{manifest.calibration_id} for track {manifest.track!r}, but the "
            f"artifact on disk is {on_disk}. Replaying against a different "
            f"calibration produces a different race."
        )

    # build_engine, not RaceEngine(...) directly: it is the same call the
    # production match job makes, so a replay cannot drift away from the
    # match it is replaying (per-track weather transitions and safety-car
    # probabilities used to be dropped here, and only here).
    engine = build_engine(manifest.track, manifest.seed)
    for participant in sorted(manifest.participants, key=lambda p: p.slot):
        if participant.house_bot is None:
            raise NotImplementedError(
                "Replaying a player bot needs its source, which Phase 2 stores "
                "against code_sha256. Golden manifests use house bots only."
            )
        bot = BUILTIN_BOTS[participant.house_bot]
        engine.add_car(
            car_id=participant.house_bot,
            player_id=participant.house_bot,
            strategy=bot["strategy"],
            starting_position=participant.slot + 1,
            starting_compound=bot["starting_compound"],
        )
    return replay_bytes(engine.run(), manifest)
