"""Sandboxed bot executor for PIT WALL.

Runs user-submitted Python strategy functions in a restricted environment:
- No imports except math, random, dataclasses
- A counted-operation budget per decision (see backend/determinism/budget.py)
- A wall-clock net that voids the match rather than deciding its outcome
- No file/network/system access
- Returns a Decision object
"""

import math
import random
import signal
import textwrap
import traceback
from dataclasses import dataclass
from typing import Optional

from RestrictedPython import compile_restricted, safe_globals
from RestrictedPython.Guards import (
    full_write_guard,
    guarded_iter_unpack_sequence,
    guarded_unpack_sequence,
    safer_getattr,
)

from backend.determinism.budget import (
    DEFAULT_DECISION_OPS,
    BudgetForfeit,
    run_with_budget,
)


# Re-export these so user code can reference them
@dataclass
class SandboxDecision:
    pit: bool
    compound: str


# Allowed builtins for user code
ALLOWED_BUILTINS = {
    "abs": abs,
    "bool": bool,
    "dict": dict,
    "enumerate": enumerate,
    "float": float,
    "int": int,
    "len": len,
    "list": list,
    "max": max,
    "min": min,
    "print": lambda *a, **kw: None,  # Silenced print
    "range": range,
    "round": round,
    "sorted": sorted,
    "str": str,
    "sum": sum,
    "tuple": tuple,
    "zip": zip,
    "True": True,
    "False": False,
    "None": None,
    "isinstance": isinstance,
}

# The wall-clock net for one decision. It is NOT the thing that decides a
# race: the counted budget in backend/determinism/budget.py is. This exists
# only for the two shapes the op counter cannot bound --
#   1. a single line that does unbounded work inside C, e.g.
#      `x = len(sorted(range(20_000)))` in a loop: 200k line events there
#      measured ~19s of real work, all of it invisible to the counter;
#   2. a bot that catches its own forfeit (CPython drops the trace function
#      as soon as a trace callback raises, so the counter gets one shot).
# Sized far above the ~20ms it takes to burn DEFAULT_DECISION_OPS of ordinary
# Python, so an honest bot that spends its whole budget is never cut short by
# the clock. When it does fire the match is voided, never completed.
DEFAULT_DECISION_WALL_MS = 2000


# Distinguishes "the bot defined no my_strategy" from "my_strategy returned
# None", without a second pass over restricted_locals outside the budget.
_MISSING_STRATEGY = object()


class TimeoutError(Exception):
    pass


class DecisionTimeout(BaseException):
    """The wall-clock net fired: this match must be voided, not completed.

    Where it lands depends on elapsed time, so recording it as a decision
    would put a machine-speed-dependent outcome into the replay -- exactly
    what the counted budget exists to prevent (spec 5.5). Derived from
    BaseException so that no `except Exception:` -- not the engine's
    per-decision handler, not a line of bot code -- can quietly downgrade a
    void into a completed race.
    """


def _timeout_handler(signum, frame):
    raise TimeoutError("Strategy function exceeded its wall-clock limit")


def compile_strategy(code: str) -> Optional[str]:
    """Compile and validate user strategy code.

    Returns error message if compilation fails, None if successful.
    """
    # Wrap user code to ensure it defines my_strategy
    try:
        byte_code = compile_restricted(
            code,
            filename="<user_strategy>",
            mode="exec",
        )
        if byte_code is None:
            return "Compilation failed: RestrictedPython rejected the code"
        return None
    except SyntaxError as e:
        return f"Syntax error: {e}"
    except Exception as e:
        return f"Compilation error: {e}"


def _guarded_getitem(obj, key):
    """Subscription guard for `obj[key]` in bot code.

    RestrictedPython rewrites attribute *syntax*, so `state.__class__` is
    caught at compile time. It never sees a name that arrives as a string at
    runtime, and `Namespace.__getitem__` maps subscription straight onto
    getattr -- so `state['__class__']` was attribute access smuggled past the
    rewriter, and from the class it is three hops to the real builtins and
    `sys.settrace`. Every underscore-prefixed string key is refused here, and
    Namespace refuses them again on its own, so neither route relies on the
    other being right.

    Non-string keys pass straight through: `beliefs[rival_id]`, `cars[0]` and
    `pit_laps[-1]` are all ordinary indexing.
    """
    if isinstance(key, str) and key.startswith("_"):
        raise KeyError(
            f'"{key}" is an invalid key because it starts with "_"'
        )
    return obj[key]


def build_sandbox_globals(seed: int, slot: int) -> dict:
    """Build the RestrictedPython globals every bot's strategy executes under.

    `seed` and `slot` exist to derive a private `random.Random` stream for
    this car (see below) -- they carry no other effect on the globals.
    """
    restricted_globals = safe_globals.copy()
    restricted_globals["__builtins__"] = ALLOWED_BUILTINS
    restricted_globals["_getattr_"] = safer_getattr
    restricted_globals["_getiter_"] = iter
    restricted_globals["_getitem_"] = _guarded_getitem
    restricted_globals["_inplacevar_"] = lambda op, x, y: op(x, y)
    restricted_globals["_unpack_sequence_"] = guarded_unpack_sequence
    restricted_globals["_iter_unpack_sequence_"] = guarded_iter_unpack_sequence
    restricted_globals["_write_"] = full_write_guard

    restricted_globals["math"] = math
    # A private stream per slot, derived from the match seed. Binding the
    # random *module* here gave every bot in the process one shared
    # generator: draw order then depended on which bots raced alongside
    # which, so no match could be reproduced from its seed.
    restricted_globals["random"] = random.Random((seed << 8) ^ slot)

    return restricted_globals


def execute_strategy(
    code: str,
    state_dict: dict,
    my_car_dict: dict,
    timeout_ms: int = DEFAULT_DECISION_WALL_MS,
    *,
    seed: int,
    slot: int,
    max_ops: int = DEFAULT_DECISION_OPS,
) -> dict:
    """Execute a user strategy function in a sandboxed environment.

    Args:
        code: User's Python code (must define `my_strategy(state, my_car)`)
        state_dict: Serialized RaceState as dict
        my_car_dict: Serialized CarState as dict
        timeout_ms: wall-clock net for this decision; see
            DEFAULT_DECISION_WALL_MS. Not a budget -- exceeding it voids the
            match instead of producing a decision.
        seed: Match seed, mixed into this car's private random stream
        slot: This car's stable index in the match, mixed into its stream
        max_ops: executed-line budget for this decision. This is the limit
            that decides races, because where it lands depends only on the
            code and its inputs.

    Returns:
        {"pit": bool, "compound": str} or {"error": str}

    Raises:
        BudgetForfeit: the bot spent its whole operation budget. Deliberately
            not swallowed into an {"error": ...}: the caller owns the lap
            number and car id this happened on, and RaceEngine turns it into
            a recorded no-op decision plus a "budget_forfeit" replay event.
        DecisionTimeout: the wall-clock net fired; the match is void.
    """
    try:
        byte_code = compile_restricted(
            code,
            filename="<user_strategy>",
            mode="exec",
        )
        if byte_code is None:
            return {"error": "Compilation failed"}
    except Exception as e:
        return {"error": f"Compilation error: {e}"}

    restricted_globals = build_sandbox_globals(seed=seed, slot=slot)

    # Make state and car available as simple namespace objects
    class Namespace:
        def __init__(self, d):
            for k, v in d.items():
                if isinstance(v, dict):
                    setattr(self, k, Namespace(v))
                elif isinstance(v, list):
                    setattr(self, k, [
                        Namespace(item) if isinstance(item, dict) else item
                        for item in v
                    ])
                else:
                    setattr(self, k, v)

        # Both accessors take the field name as a *string*, which is the one
        # thing RestrictedPython's rewriting cannot see. Routing them through
        # safer_getattr applies the same rules the compiler applies to
        # attribute syntax: no underscore-prefixed names, no frame or code
        # introspection attributes. No RaceState or CarState field starts
        # with "_" (see engine/serialize.py), so nothing legitimate is lost.
        def __getitem__(self, key):
            return safer_getattr(self, key, None)

        def get(self, key, default=None):
            return safer_getattr(self, key, default)

    restricted_globals["state"] = Namespace(state_dict)
    restricted_globals["my_car"] = Namespace(my_car_dict)

    # Execute under the counted budget, with the wall-clock net outside it
    restricted_locals = {}

    # Set alarm for the wall-clock net (Unix, main thread only)
    old_handler = None
    try:
        old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
        # Convert ms to microseconds for setitimer
        signal.setitimer(signal.ITIMER_REAL, timeout_ms / 1000.0)
    except (ValueError, AttributeError):
        pass  # Signal not available (Windows, or not main thread)

    def _define_and_decide():
        """Everything the bot gets to run, under one counted budget.

        The module body is inside the budget too, not just the call: a bot
        whose runaway loop sits at module level costs exactly as much as one
        that hides it in my_strategy, and both must trip the same counter.
        """
        exec(byte_code, restricted_globals, restricted_locals)
        strategy_fn = restricted_locals.get("my_strategy")
        if strategy_fn is None:
            return _MISSING_STRATEGY
        return strategy_fn(
            restricted_globals["state"],
            restricted_globals["my_car"],
        )

    try:
        result, _ops_used = run_with_budget(_define_and_decide, (), max_ops)

        if result is _MISSING_STRATEGY:
            return {"error": "Code must define a function called 'my_strategy'"}

        # Parse result
        if isinstance(result, dict):
            return {
                "pit": bool(result.get("pit", False)),
                "compound": str(result.get("compound", "MEDIUM")),
            }
        elif hasattr(result, "pit"):
            return {
                "pit": bool(result.pit),
                "compound": str(getattr(result, "compound", "MEDIUM")),
            }
        else:
            return {"error": f"my_strategy must return a dict with 'pit' and 'compound' keys"}

    except BudgetForfeit:
        # Explicit, even though BudgetForfeit is a BaseException and the
        # handler below would not catch it: the forfeit belongs to the
        # caller, which knows the lap and car it happened to.
        raise
    except TimeoutError:
        raise DecisionTimeout(
            f"a decision exceeded the {timeout_ms}ms wall-clock limit"
        ) from None
    except Exception as e:
        return {"error": f"Runtime error: {type(e).__name__}: {e}"}
    finally:
        # Cancel alarm
        try:
            signal.setitimer(signal.ITIMER_REAL, 0)
            if old_handler is not None:
                signal.signal(signal.SIGALRM, old_handler)
        except (ValueError, AttributeError):
            pass


# Default user strategy template
STRATEGY_TEMPLATE = '''\
def my_strategy(state, my_car):
    """Your strategy function.

    Args:
        state: RaceState with fields:
            .lap (int), .total_laps (int), .track (str),
            .weather (str: 'dry'|'damp'|'wet'|'drying'),
            .safety_car (bool), .safety_car_laps_left (int),
            .track_temp (float), .cars (list of CarState)

        my_car: CarState with fields:
            .car_id (str), .position (int), .gap_to_leader (float),
            .compound (str), .tyre_age (int), .fuel_kg (float),
            .pit_count (int), .pit_laps (list), .last_lap_time (float),
            .total_time (float), .retired (bool), .drs_available (bool),
            .compounds_used (list),
            .beliefs (dict of rival_id -> belief):
                .estimated_tyre_age (float) - Bayesian estimate of rival tyre laps
                .estimated_compound (str) - Inferred compound from deg rate
                .pit_probability_next_5_laps (float) - Logistic pit probability
                .confidence (float) - Belief certainty (0-1)
                .estimated_deg_rate (float) - Learned deg rate (s/lap)
                .undercut_viable (bool) - Is undercut feasible?
                .undercut_gain (float) - Estimated time gain from undercut
                .optimal_pit_in (int) - Est. laps until rival pits

    Returns:
        dict with keys: pit (bool), compound (str)
    """
    remaining = state.total_laps - state.lap

    # Switch to intermediates in wet conditions
    if state.weather == "wet" and my_car.compound in ("SOFT", "MEDIUM", "HARD"):
        if my_car.tyre_age >= 3 and remaining > 5:
            return {"pit": True, "compound": "INTERMEDIATE"}

    # Switch back to slicks when dry
    if state.weather == "dry" and my_car.compound == "INTERMEDIATE":
        new_compound = "MEDIUM" if remaining > 15 else "SOFT"
        return {"pit": True, "compound": new_compound}

    # Free pit stop under safety car
    if state.safety_car and my_car.tyre_age >= 10 and remaining > 5:
        if remaining > 25:
            new_compound = "HARD"
        elif remaining > 15:
            new_compound = "MEDIUM"
        else:
            new_compound = "SOFT"
        return {"pit": True, "compound": new_compound}

    # Check for undercut opportunities using the belief system
    for rival in state.cars:
        if rival.car_id == my_car.car_id or rival.retired:
            continue
        belief = my_car.beliefs.get(rival.car_id, {})
        if belief.get("undercut_viable") and belief.get("undercut_gain", 0) > 2.0:
            if remaining > 10 and my_car.tyre_age > 10 and my_car.pit_count < 2:
                new_compound = "HARD" if remaining > 20 else "MEDIUM"
                return {"pit": True, "compound": new_compound}

    # Pit based on tyre degradation cliff zones
    # SOFT cliff ~12 laps, MEDIUM cliff ~20 laps, HARD cliff ~30 laps
    cliff_ages = {"SOFT": 12, "MEDIUM": 20, "HARD": 30}
    cliff = cliff_ages.get(my_car.compound, 20)
    if my_car.tyre_age >= cliff and remaining > 5:
        if remaining > 25:
            new_compound = "HARD"
        elif remaining > 15:
            new_compound = "MEDIUM"
        else:
            new_compound = "SOFT"
        return {"pit": True, "compound": new_compound}

    return {"pit": False, "compound": my_car.compound}
'''
