"""Canonical replay artifact: the thing whose sha256 the contract is about.

The manifest is embedded rather than referenced so a replay is verifiable on
its own — re-running the embedded manifest must reproduce these exact bytes.
"""

import hashlib
from dataclasses import asdict

from .canonical import canonical_json
from .manifest import MatchManifest
from ..engine.race import RaceResult
from ..engine.serialize import car_state_to_dict

REPLAY_FORMAT_VERSION = "1.0.0"


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
    # Imported here, not at module scope: the engine pulls in the track data
    # and calibration store, and replay.py is imported by the manifest tests.
    from ..engine.bots import BUILTIN_BOTS
    from ..engine.cli_runner import build_track_physics
    from ..engine.race import RaceEngine

    engine = RaceEngine(track=build_track_physics(manifest.track), seed=manifest.seed)
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
