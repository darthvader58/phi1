"""A player row with no starting compound must not reach the engine as None.

Pre-branch this read `pdata.get("starting_compound", "MEDIUM")`; the rewrite
dropped the default. The worker's own
`participant.get("starting_compound", default_compound)` could not recover
it either, and for the reason that makes this class of bug survive review: a
dict default only fires when the KEY IS ABSENT, and here the key is present
with value None. Unreachable today only because JoinRaceRequest's field is
non-optional with a "MEDIUM" default -- one field ever becoming optional
makes it live, and the symptom is a compound of None inside engine.add_car.
"""

from backend.worker import _spec_from_job


def test_a_player_with_no_compound_gets_medium_in_the_job():
    """Red line: the `or "MEDIUM"` in main._build_job_and_manifest's
    starting_compound entry. Change it to `.get("starting_compound",
    "MEDIUM")` -- the pre-branch form -- and this goes red, because the key
    is present and None.
    """
    from backend.main import _build_job_and_manifest

    lobby = {
        "track": "bahrain",
        "players": {
            "p1": {"username": "alex", "car_id": "USR-01", "code": "",
                   "starting_compound": None},
        },
        "seed": 1000,
    }
    job, _manifest = _build_job_and_manifest("m_compound", lobby)
    player = next(p for p in job["participants"] if p.get("player_id") == "p1")
    assert player["starting_compound"] == "MEDIUM"


def test_the_worker_also_refuses_to_pass_none_through():
    """Two independent places had the same dict-default bug, so both are
    pinned: a job built by an older API replica can still carry an explicit
    None.

    Red line: the `participant.get("starting_compound") or default_compound`
    expression in worker._spec_from_job.
    """
    job = {
        "match_id": "m_compound", "track": "bahrain", "seed": 1000,
        "participants": [
            {"slot": 0, "player_id": "p1", "car_id": "USR-01", "code": "",
             "starting_compound": None},
            {"slot": 1, "house_bot": "NXS-07", "starting_compound": None},
        ],
    }
    spec = _spec_from_job(job)
    assert spec["cars"][0]["starting_compound"] == "MEDIUM"
    assert spec["cars"][1]["starting_compound"] is not None, (
        "a house bot's own fixed compound must survive an explicit None"
    )


def test_an_explicit_compound_is_still_honoured():
    """The guard must not have replaced a real choice with the default."""
    job = {
        "match_id": "m_compound", "track": "bahrain", "seed": 1000,
        "participants": [
            {"slot": 0, "player_id": "p1", "car_id": "USR-01", "code": "",
             "starting_compound": "SOFT"},
        ],
    }
    assert _spec_from_job(job)["cars"][0]["starting_compound"] == "SOFT"
