"""The car_id uniqueness invariant, stated once, for every source of one.

**No two cars in one race may share a car_id.**

car_id is an engine identity, not a display label. engine/race.py's
`add_car` does `self.strategies[car_id] = strategy` and
`self.belief_models[car_id] = BeliefModel(...)`, and `_build_race_state`
reads `self.belief_models[car.car_id]` back for every car -- so two cars
under one id meant the second `add_car` silently won and one strategy
function plus one shared BeliefModel drove both of them.

Where a duplicate ends up, precisely, because a determinism codebase
should not be vague about which artefact a value reaches:

  * the REPLAY hash -- yes. `replay_bytes` serialises `final_standings`
    through `car_state_to_dict`, which carries `car_id`.
  * the MANIFEST hash -- no. `determinism/manifest.Participant` has five
    fields (slot, player_id, bot_version_id, code_sha256, house_bot) and
    no car_id, so the manifest hash is independent of every car_id
    choice. An earlier version of this file said "hashed into the
    manifest"; it was wrong, and the guard's placement does not depend
    on it (see THE LOBBY CHOKEPOINT below).
  * `race_results` -- two rows with the same car_id and distinct
    player_id, which the (race_id, player_id) unique index does not
    catch.

This invariant has been broken three times, and each break was a *source*
of car_ids that the previous fix did not know about:

  round 3 -- two concurrent brand-new joins computing the same default
             from a pre-write snapshot
  round 4 -- a re-join recomputing a default, renaming that player and
             freeing their old label for the next joiner to be handed too
  round 5 -- a player explicitly claiming a HOUSE BOT's identity, which
             the lobby's "is this label taken?" set never covered because
             house bots are not lobby players

Each was fixed for the case that had been reported, and each fix then
claimed more than it enforced. So the rule lives here, and so does an
honest map of exactly what enforces it where. Read the map as: prevention
is per-source and is what gives a caller a good error; enforcement is the
line below that no grid can route around.

PREVENTION, per source of a car_id
----------------------------------
  SOURCE 1 -- lobby players.
      `LobbyStore.join()` decides every player's car_id inside one atomic
      Lua script. It is handed RESERVED_CAR_IDS below and seeds its
      "already taken" set with them, so a player can neither be assigned
      nor explicitly claim one, and the set is completed from the other
      players already in the lobby.

  SOURCE 2 -- house bots, appended to every race until the grid is full
      by `main._build_job_and_manifest`, each carrying `car_id == bot_id`
      (`worker._spec_from_job`: `participant.get("car_id") or house_bot`).
      These identities are fixed constants, which is what makes them safe
      to hand to source 1 as a pre-computed set.

THE LOBBY CHOKEPOINT -- `assert_unique_car_ids()` in
`main._build_job_and_manifest`
------------------------------------------------------------------
Sources 1 and 2 converge there, and it is the last point before the JOB
is built -- the job being what carries car_id to the worker. It exists
to fail EARLY and cleanly: in the API process, before `save_manifest`
and before `enqueue`, so a bad grid degrades to `_run_race`'s existing
abort path with no manifest row and no stranded queue entry. It covers
every lobby-originated grid and nothing else, which is all it claims.

THE ENFORCEMENT LINE -- `RaceEngine.add_car`
--------------------------------------------
Four sites in this codebase assemble a grid, and only one of them is the
lobby path:

    main._build_job_and_manifest -> worker._spec_from_job   lobby + bots
    main.test_bot (/api/test-bot)                           "USER" + bots
    engine.cli_runner.main                                  bots only
    determinism.replay.replay_from_manifest                 bots only

The last three are safe by construction today -- their ids are hardcoded
constants with no request-supplied car_id anywhere -- but "audited safe"
is what the three rounds above each believed, and `/api/test-bot` taking
a caller-chosen car_id is the obvious next feature for it. So the
property is not asserted about the call sites at all. `add_car` refuses a
car_id already in the race, and every one of those four grids reaches
`add_car`, as does anything a fifth ever builds. That also closes the
consumer side: `worker._spec_from_job` re-derives ids without re-checking
them, so the guarantee used to be "nothing else enqueues" rather than
"the worker is safe against what it is handed"; now it is the latter.
"""

from typing import Iterable


class DuplicateCarIdError(Exception):
    """Two cars in one race were given the same car_id.

    Raised by assert_unique_car_ids() when a grid is assembled, and by
    RaceEngine.add_car when one reaches the engine by any route."""


def _reserved_car_ids() -> frozenset:
    # Imported inside the function: engine/bots.py imports engine/race.py,
    # which imports DuplicateCarIdError from this module, so reading
    # BUILTIN_BOTS at module scope here would close that loop. Called once
    # below, at import time, so RESERVED_CAR_IDS is still a constant.
    from ..engine.bots import BUILTIN_BOTS

    return frozenset(BUILTIN_BOTS)


# The identities house bots bring to every race. Derived from BUILTIN_BOTS
# rather than written out, so adding a sixth bot reserves its id in the
# same commit that creates it.
RESERVED_CAR_IDS = _reserved_car_ids()


def assert_unique_car_ids(car_ids: Iterable[str]) -> None:
    """Refuse a grid that puts two cars under one engine identity.

    Iterates in the caller's order and reports the first repeat rather
    than diffing two sets, so the error names the actual collision.
    """
    seen = set()
    for car_id in car_ids:
        if car_id in seen:
            raise DuplicateCarIdError(
                f"two cars in this race share car_id {car_id!r} -- one "
                f"bot would drive both (engine/race.py keys strategies "
                f"and belief_models by car_id); refusing to build a job "
                f"over it"
            )
        seen.add(car_id)
