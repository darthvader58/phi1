"""The manifest the API persists and the manifest the worker reconstructs
from the same job must hash to the same value.

Review round 2, F3: `_build_job_and_manifest` computed code_sha256 for its
own Participant list but never put it in the job's participant dicts, so
`_manifest_from_job` (backend/worker.py) read it back as None. The digest
`save_replay_hash` stores was therefore the hash of a replay whose embedded
manifest does not match the manifest row already saved under the same
match_id -- a manifest whose hash does not match its own replay is worse
than no hash, because it will be trusted.

Pure-function test: _build_job_and_manifest and _manifest_from_job take
plain dicts and return dataclasses, no Redis or Mongo involved, so this
runs unconditionally.
"""

from backend.determinism.manifest import manifest_sha256
from backend.main import _build_job_and_manifest
from backend.worker import _manifest_from_job

PLAYER_CODE = (
    "def my_strategy(state, my_car):\n"
    "    return {'pit': False, 'compound': 'MEDIUM'}\n"
)


def _lobby(track="bahrain", players=None):
    return {
        "race_id": "m_manifest_consistency",
        "track": track,
        "players": players or {},
    }


def test_the_api_manifest_and_the_worker_manifest_agree_with_a_player_car():
    lobby = _lobby(players={
        "p1": {"car_id": "USR-01", "code": PLAYER_CODE,
               "starting_compound": "MEDIUM"},
    })
    job, api_manifest = _build_job_and_manifest("m_manifest_consistency", lobby)
    worker_manifest = _manifest_from_job(job)

    assert manifest_sha256(worker_manifest) == manifest_sha256(api_manifest), (
        "the manifest embedded in the replay must be byte-identical to the "
        "manifest row saved for this match_id"
    )
    # And specifically: code_sha256 must have actually traveled through the
    # job, not merely have canceled out somehow. A None on both sides would
    # make the two manifests agree (both wrong) without this.
    assert job["participants"][0]["code_sha256"] is not None
    assert (
        job["participants"][0]["code_sha256"]
        == api_manifest.participants[0].code_sha256
        == worker_manifest.participants[0].code_sha256
    )


def test_the_api_manifest_and_the_worker_manifest_agree_with_only_house_bots():
    lobby = _lobby(players={})
    job, api_manifest = _build_job_and_manifest("m_manifest_consistency_bots", lobby)
    worker_manifest = _manifest_from_job(job)

    assert manifest_sha256(worker_manifest) == manifest_sha256(api_manifest)


def test_the_api_manifest_and_the_worker_manifest_agree_with_mixed_participants():
    lobby = _lobby(players={
        "p1": {"car_id": "USR-01", "code": PLAYER_CODE, "starting_compound": "SOFT"},
        "p2": {"car_id": "USR-02", "code": PLAYER_CODE + "    pass\n",
               "starting_compound": "HARD"},
    })
    job, api_manifest = _build_job_and_manifest("m_manifest_consistency_mixed", lobby)
    worker_manifest = _manifest_from_job(job)

    assert manifest_sha256(worker_manifest) == manifest_sha256(api_manifest)
    # Two different players' code must not hash the same.
    codes = {p["code_sha256"] for p in job["participants"] if p.get("code")}
    assert len(codes) == 2
