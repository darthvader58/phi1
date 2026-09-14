"""The car_id uniqueness invariant, stated once, for every source of one.

**No two cars in one race may share a car_id.**

car_id is an engine identity, not a display label. engine/race.py's
`add_car` does `self.strategies[car_id] = strategy` and
`self.belief_models[car_id] = BeliefModel(...)`, and `_build_race_state`
reads `self.belief_models[car.car_id]` back for every car -- so two cars
under one id means the second `add_car` silently wins and one strategy
function plus one shared BeliefModel drives both of them. That is then
hashed into the manifest and the replay, and `race_results` takes two rows
with the same car_id (distinct player_id, so the unique index there does
not catch it either).

This invariant has been broken three times, and each break was a *source*
of car_ids that the previous fix did not know about:

  round 3 -- two concurrent brand-new joins computing the same default
             from a pre-write snapshot
  round 4 -- a re-join recomputing a default, renaming that player and
             freeing their old label for the next joiner to be handed too
  round 5 -- a player explicitly claiming a HOUSE BOT's identity, which
             the lobby's "is this label taken?" set never covered because
             house bots are not lobby players

Each of those was fixed for the case that had been reported. So the rule
lives here now instead, with every source enforced against it:

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

  THE CHOKEPOINT -- `assert_unique_car_ids()`.
      Every source converges in `_build_job_and_manifest`, the one place
      that assembles the whole grid, and it refuses to seal a manifest
      over a duplicate. A THIRD source cannot be added without passing
      through this call, which is the property the three rounds above
      were missing: prevention lives with each source, but detection
      lives in one place that sees all of them.
"""

from typing import Iterable

from ..engine.bots import BUILTIN_BOTS


class DuplicateCarIdError(Exception):
    """Two cars in one race were assembled under the same car_id.

    Raised by assert_unique_car_ids() rather than letting the race start:
    a duplicate here would be sealed into the manifest and the replay
    hash, so refusing is the only way to keep those meaningful."""


# The identities house bots bring to every race. Derived from BUILTIN_BOTS
# rather than written out, so adding a sixth bot reserves its id in the
# same commit that creates it.
RESERVED_CAR_IDS = frozenset(BUILTIN_BOTS)


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
                f"and belief_models by car_id); refusing to build a "
                f"manifest over it"
            )
        seen.add(car_id)
