"""A rival's car_id becomes an attribute name inside every bot's sandbox.

Belief dicts are keyed by rival car_id, and Namespace turns every key of a
dict it is handed into an attribute. An unconstrained car_id was therefore an
attacker-controlled runtime string reaching a reflective operation
RestrictedPython never sees -- the same bug class as the `state['__class__']`
escape, but on the *write* side, and one HTTP request wide:

    rival car_id "get"       -> shadows Namespace.get, so `my_car.beliefs.get(...)`
                                (the line in the shipped STRATEGY_TEMPLATE)
                                raises for every opponent, every lap
    rival car_id "__class__" -> setattr raises TypeError from Namespace.__init__,
                                which runs before execute_strategy's try block,
                                so it aborts the whole match

Two layers are tested here: the API boundary refuses such an id, and
Namespace refuses to install one even if a belief key reaches it another way
(a replayed manifest, a spec assembled outside the API).

The model is exercised directly rather than through TestClient, so this file
needs no database and no app startup.
"""

import pytest
from pydantic import ValidationError

from backend.main import CAR_ID_PATTERN, JoinRaceRequest
from backend.sandbox.runner import (
    NAMESPACE_RESERVED_KEYS,
    Namespace,
    execute_strategy,
)


# ─── Layer 1: the API boundary ────────────────────────────────────────

HOSTILE_IDS = [
    "get",          # shadows the accessor every bot uses on beliefs
    "__class__",    # setattr raises TypeError out of Namespace.__init__
    "__init__",
    "_private",     # safer_getattr refuses these, so they are dead weight
    "has space",
    "x" * 17,       # unbounded ids are a display and log problem too
    "",
    "drop;table",
]


@pytest.mark.parametrize("car_id", HOSTILE_IDS, ids=repr)
def test_join_rejects_a_hostile_car_id(car_id):
    with pytest.raises(ValidationError):
        JoinRaceRequest(car_id=car_id)


@pytest.mark.parametrize("car_id", ["VEL-01", "P01", "a", "A1_b-2", "x" * 16])
def test_join_still_accepts_ordinary_car_ids(car_id):
    """The guard must not break the ids the game already issues: house bots
    are "VEL-01"-shaped and join's own default is "P01"-shaped."""
    assert JoinRaceRequest(car_id=car_id).car_id == car_id


def test_car_id_is_optional():
    """join() generates one when the request omits it."""
    assert JoinRaceRequest().car_id is None


def test_the_generated_default_satisfies_the_pattern():
    """join() falls back to f"P{n:02d}"; if that failed its own constraint the
    endpoint would be unusable without an explicit id."""
    import re
    for n in (1, 8, 99):
        assert re.match(CAR_ID_PATTERN, f"P{n:02d}")


# ─── Layer 2: Namespace itself ────────────────────────────────────────

def test_namespace_refuses_to_install_a_reserved_key():
    """Even reaching Namespace directly, `get` must survive as the accessor."""
    ns = Namespace({"get": "hijacked", "compound": "SOFT"})
    assert callable(ns.get)
    assert ns.get("compound") == "SOFT"


def test_namespace_refuses_to_install_an_underscore_key():
    """`setattr(self, "__class__", ...)` raises TypeError from a constructor
    that runs outside execute_strategy's try block, aborting the match."""
    ns = Namespace({"__class__": {"a": 1}, "compound": "SOFT"})
    assert ns.__class__ is Namespace
    assert ns.get("compound") == "SOFT"


def test_namespace_reserved_keys_covers_every_public_attribute():
    """Derived from the class, so a method added later is covered; pinned
    here so an attribute that stops being protected is noticed."""
    assert "get" in NAMESPACE_RESERVED_KEYS
    assert NAMESPACE_RESERVED_KEYS == frozenset(
        n for n in dir(Namespace) if not n.startswith("_")
    )


def test_namespace_keeps_hyphenated_rival_ids_reachable():
    """House bot ids contain a hyphen, so the filter must not be an
    identifier check -- beliefs are read by exactly these keys."""
    ns = Namespace({"beliefs": {"VEL-01": {"undercut_viable": True}}})
    assert ns.beliefs.get("VEL-01").get("undercut_viable") is True


# ─── The two layers together, through a real bot ──────────────────────

READS_BELIEFS = (
    "def my_strategy(state, my_car):\n"
    "    hits = 0\n"
    "    for rival in state.cars:\n"
    "        belief = my_car.beliefs.get(rival.car_id, {})\n"
    "        if belief.get('undercut_viable'):\n"
    "            hits = hits + 1\n"
    "    return {'pit': hits > 0, 'compound': 'SOFT'}\n"
)


@pytest.mark.parametrize("hostile", ["get", "__class__"])
def test_a_hostile_rival_id_cannot_break_another_bots_strategy(
    sample_state, sample_car, hostile
):
    """The end-to-end property: a hostile string arriving as a belief key must
    not stop an opponent's `my_car.beliefs.get(...)` from working.

    Before the fix, a belief keyed "get" shadowed the accessor and the
    template's own line came back as
    {'error': "TypeError: 'Namespace' object is not callable"}; a belief
    keyed "__class__" raised TypeError out of Namespace.__init__, which runs
    before execute_strategy's try block, so it escaped the sandbox wrapper
    entirely and took the match down with it.

    The rivals in state.cars keep ordinary ids: after the boundary fix no
    car in a real race can be named either of these, and this is the layer
    that has to hold if a belief key reaches Namespace some other way.
    """
    car = dict(sample_car)
    car["beliefs"] = {
        hostile: {"undercut_viable": True},
        "c2": {"undercut_viable": True},
    }
    state = dict(sample_state)
    state["cars"] = [car, {**car, "car_id": "c2", "beliefs": {}}]

    result = execute_strategy(READS_BELIEFS, state, car, seed=42, slot=0)
    assert "error" not in result, result
    # The real rival's belief was still readable: the hostile key was
    # dropped, not the whole dict.
    assert result["pit"] is True
