"""Sandboxed bot executor for PIT WALL.

Runs user-submitted Python strategy functions in a restricted environment:
- No imports except math, random, dataclasses
- A counted-operation budget per decision (see backend/determinism/budget.py)
- A wall-clock net that voids the match rather than deciding its outcome
- No file/network/system access
- Returns a Decision object
"""

import math
import operator
import types
import os
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

from backend.sandbox import isolation
from backend.determinism.signals import MatchVoiding
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
    # Exception classes. Without these a bot cannot write `except ValueError:`
    # and is forced into a bare `except:` — which is precisely the construct
    # that swallows the wall-clock signal, so their absence pushed authors
    # toward the one pattern the resource limits least want to see.
    #
    # BaseException is deliberately NOT here, and must never be added: the
    # operation budget (BudgetForfeit) and the wall-clock net (_WallClockFired)
    # are BaseExceptions specifically so `except Exception` cannot swallow
    # them. Exposing BaseException would let a bot decline its own limits.
    "Exception": Exception,
    "ArithmeticError": ArithmeticError,
    "AttributeError": AttributeError,
    "IndexError": IndexError,
    "KeyError": KeyError,
    "LookupError": LookupError,
    "RuntimeError": RuntimeError,
    "StopIteration": StopIteration,
    "TypeError": TypeError,
    "ValueError": ValueError,
    "ZeroDivisionError": ZeroDivisionError,
    "AssertionError": AssertionError,
    "NameError": NameError,
    "NotImplementedError": NotImplementedError,
    "OverflowError": OverflowError,
    "RecursionError": RecursionError,
    "UnboundLocalError": UnboundLocalError,
}


# RestrictedPython rewrites `x += y` into `_inplacevar_('+=', x, y)`, passing
# the operator as a STRING. The previous guard was `lambda op, x, y: op(x, y)`,
# which called the string — so every augmented assignment a player wrote failed
# with "'str' object is not callable".
#
# The values are the NON-in-place operators on purpose. operator.iadd would
# call x.__iadd__, handing control to whatever object the bot holds a reference
# to; operator.add never invokes it. The cost is value semantics — `a += b` is
# `a = a + b`, so a list alias does not observe the append — which is a
# defensible thing for a sandbox to guarantee and is pinned by a test.
_INPLACE_OPS = {
    "+=": operator.add,
    "-=": operator.sub,
    "*=": operator.mul,
    "/=": operator.truediv,
    "//=": operator.floordiv,
    "%=": operator.mod,
    "**=": operator.pow,
    "<<=": operator.lshift,
    ">>=": operator.rshift,
    "&=": operator.and_,
    "|=": operator.or_,
    "^=": operator.xor,
}


def _guarded_inplacevar(op: str, x, y):
    """Evaluate an augmented assignment from an allowlist of operators.

    Unknown operators raise rather than falling through: `@=` (matrix multiply)
    is excluded because nothing a bot can hold implements it and an allowlist
    that grows by accident is not an allowlist.
    """
    try:
        apply_op = _INPLACE_OPS[op]
    except KeyError:
        raise ValueError(f"operator {op!r} is not permitted in strategy code")
    return apply_op(x, y)


def _guarded_getattr(obj, name, *args, **kwargs):
    """safer_getattr, plus a refusal to read attributes off a class object.

    safer_getattr blocks leading-underscore names and RestrictedPython's
    INSPECT_ATTRIBUTES set. `mro` is in neither: it is an ordinary public
    method on every type. Once exception classes became nameable that was
    enough to escape the sandbox's exception model entirely --
    `Exception.mro()[1]` is BaseException, and a bot holding BaseException can
    raise past the operation budget's forfeit latch and past RaceEngine's
    per-car handler, voiding anyone's match on demand and having it recorded
    as an engine fault.

    Blocking `mro` alone would fix today's route. Refusing the whole class
    surface fixes the shape: any public method a type gains, now or in a
    future Python, is refused by default rather than by enumeration. Nothing a
    bot legitimately does needs it -- classes are for calling (`ValueError(x)`,
    `int(s)`), and attributes belong to the instances they produce.
    """
    # GenericAlias and not just type: isinstance(list[int], type) is False in
    # 3.11+, and GenericAlias forwards attribute reads to its origin, so
    # `list[int].mro()` walked straight past a bare isinstance(obj, type)
    # check. Nothing dangerous was reachable through it today, but leaving it
    # would mean the guard covered the instance and not the shape.
    if isinstance(obj, (type, types.GenericAlias)):
        raise AttributeError(
            f"attribute {name!r} is not readable on a class inside strategy "
            f"code. Classes are for calling -- use an instance, or a lambda "
            f"such as `key=lambda s: s.lower()` instead of `key=str.lower`"
        )
    return safer_getattr(obj, name, *args, **kwargs)


class _SilentPrint:
    """Satisfies RestrictedPython's print protocol and discards the output.

    `print` was in ALLOWED_BUILTINS but unusable: RestrictedPython rewrites
    print statements to go through `_print_`, which was never installed, so
    every print raised NameError and the silenced builtin was dead code.
    """

    def __init__(self, _getattr_=None):
        pass

    def write(self, text):
        pass

    def _call_print(self, *args, **kwargs):
        pass

    def __call__(self):
        return ""


def _guarded_apply(fn, *args, **kwargs):
    """Support f(*args) / f(**kwargs), which RestrictedPython routes here.

    This grants no reach a bot did not already have: fn is whatever it could
    already name and call directly, and the arguments are its own values.
    """
    return fn(*args, **kwargs)

# The wall-clock net for one decision. It is NOT the thing that decides a
# race: the counted budget in backend/determinism/budget.py is. This exists
# only for the two shapes the op counter cannot bound --
#   1. a single line that does unbounded work inside C, e.g.
#      `x = len(sorted(range(20_000)))` in a loop: 200k line events there
#      measured ~19s of real work, all of it invisible to the counter;
#   2. a bot that catches its own forfeit (CPython drops the trace function
#      as soon as a trace callback raises, so the counter gets one shot).
# The net is not a single catchable exception -- a bare `except:` would eat
# that as readily as it eats the forfeit. It repeats, it latches, and in the
# isolated child a bot still running one interval later is exited outright.
# _make_wall_clock_handler has the details.
# Sized far above the ~20ms it takes to burn DEFAULT_DECISION_OPS of ordinary
# Python, so an honest bot that spends its whole budget is never cut short by
# the clock. When it does fire the match is voided, never completed.
DEFAULT_DECISION_WALL_MS = 2000


# Distinguishes "the bot defined no my_strategy" from "my_strategy returned
# None", without a second pass over restricted_locals outside the budget.
_MISSING_STRATEGY = object()


class _WallClockFired(MatchVoiding):
    """Raised inside the bot by the SIGALRM handler when the net fires.

    A BaseException, and not the builtin TimeoutError it used to shadow, for
    two reasons. It must not be reinterpreted by run_with_budget's "the bot
    swallowed its forfeit" recovery -- a fired net means unbounded time was
    consumed, which outranks a forfeit and has to void rather than be
    recorded. And no `except Exception:` between here and execute_strategy
    should be able to absorb it.
    """


class DecisionTimeout(MatchVoiding):
    """The wall-clock net fired: this match must be voided, not completed.

    Where it lands depends on elapsed time, so recording it as a decision
    would put a machine-speed-dependent outcome into the replay -- exactly
    what the counted budget exists to prevent (spec 5.5). Derived from
    BaseException so that no `except Exception:` -- not the engine's
    per-decision handler, not a line of bot code -- can quietly downgrade a
    void into a completed race.
    """


def _make_wall_clock_handler(timeout_ms: int):
    """Build the SIGALRM handler for one decision, plus its latch.

    A single alarm is not enough on its own. Raising _WallClockFired into
    the bot is only a *request* to stop: a bare `except:` in bot code catches it
    exactly as readily as it catches BudgetForfeit, and RestrictedPython
    permits bare except. A bot looping inside `try: ... except: pass` ran for
    45s against an 800ms alarm, because the alarm fired once and its
    exception was eaten.

    So the alarm repeats, and what actually holds is:

    * the latch. Once the net has fired, `state["fired"]` stays set, so no
      matter what the bot swallows, the decision is voided the moment control
      comes back to us -- whether it returns or raises.
    * the second firing. A bot still running one interval after being told to
      stop is provably ignoring the signal, and no in-process exception will
      ever reach it. Inside the isolated child there is one door a bare
      `except:` cannot cover, and it takes it: the process exits with
      WALL_CLOCK_VOID_EXITCODE, which isolation.py classifies as the bot's
      limit breach rather than as a crash of ours.

    Outside the isolated child -- the API process, or a test run -- exiting
    would take down a process that is not ours to end, so there the handler
    only keeps raising. Nothing is lost: the API process never executes bots
    (signal.signal cannot arm off the main thread), and the isolated child is
    the only place a match actually runs.
    """
    state = {"fired": 0}

    def handler(signum, frame):
        state["fired"] += 1
        if state["fired"] > 1 and isolation.IN_ISOLATED_CHILD:
            os._exit(isolation.WALL_CLOCK_VOID_EXITCODE)
        raise _WallClockFired(
            f"strategy function exceeded its {timeout_ms}ms wall-clock limit"
        )

    return handler, state


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


class Namespace:
    """Attribute view over a state dict, handed to bot code as `state`/`my_car`.

    Every key becomes an attribute, and not every key is ours to trust: a
    car's `beliefs` dict is keyed by *rival car ids*, which are strings
    another player chose. `setattr(self, "get", ...)` would replace the
    accessor below for every bot in the race -- the shipped
    STRATEGY_TEMPLATE calls `my_car.beliefs.get(rival.car_id, {})` -- and
    `setattr(self, "__class__", ...)` raises TypeError out of the
    constructor, which runs *before* execute_strategy's try block and so
    escapes it entirely, aborting the whole match. One join request with a
    chosen car_id could neutralise every opponent.

    The API constrains car_id at the boundary, but belief keys reach this
    constructor from more than one path (a replayed manifest, a match spec
    assembled elsewhere), so unsafe keys are refused here as well. They are
    dropped rather than raised on: raising from a constructor that sits
    outside the try block is the failure mode being closed.
    """

    def __init__(self, d):
        for k, v in d.items():
            if not _is_safe_namespace_key(k):
                continue
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
        return _guarded_getattr(self, key, None)

    def get(self, key, default=None):
        return _guarded_getattr(self, key, default)


# Everything Namespace itself defines that a key could overwrite. Derived
# from the class so a method added later is covered without anyone
# remembering to update a literal.
NAMESPACE_RESERVED_KEYS = frozenset(
    name for name in dir(Namespace) if not name.startswith("_")
)


def _is_safe_namespace_key(key) -> bool:
    """A dict key that may become an attribute of a Namespace.

    Underscore-prefixed names are refused for the same reason
    `_guarded_getitem` refuses them -- `__class__` is three hops from the
    real builtins -- and reserved names are refused so no key can shadow the
    accessors bot code relies on.
    """
    return (
        isinstance(key, str)
        and not key.startswith("_")
        and key not in NAMESPACE_RESERVED_KEYS
    )


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
    # .copy(): a bot's globals must never hold a reference to the module-level
    # dict, or one future gap would poison every later bot in the process.
    restricted_globals["__builtins__"] = ALLOWED_BUILTINS.copy()
    restricted_globals["_getattr_"] = _guarded_getattr
    restricted_globals["_getiter_"] = iter
    restricted_globals["_getitem_"] = _guarded_getitem
    restricted_globals["_inplacevar_"] = _guarded_inplacevar
    restricted_globals["_apply_"] = _guarded_apply
    restricted_globals["_print_"] = _SilentPrint
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
    restricted_globals["state"] = Namespace(state_dict)
    restricted_globals["my_car"] = Namespace(my_car_dict)

    # Execute under the counted budget, with the wall-clock net outside it

    # Arm the wall-clock net (Unix, main thread only). The alarm *repeats*:
    # one shot would be a single catchable exception, and a bot is free to
    # catch it. See _make_wall_clock_handler.
    wall_handler, wall_state = _make_wall_clock_handler(timeout_ms)
    old_handler = None
    try:
        old_handler = signal.signal(signal.SIGALRM, wall_handler)
        seconds = timeout_ms / 1000.0
        signal.setitimer(signal.ITIMER_REAL, seconds, seconds)
    except (ValueError, AttributeError):
        pass  # Signal not available (Windows, or not main thread)

    def _define_and_decide():
        """Everything the bot gets to run, under one counted budget.

        The module body is inside the budget too, not just the call: a bot
        whose runaway loop sits at module level costs exactly as much as one
        that hides it in my_strategy, and both must trip the same counter.
        """
        # One namespace, not globals+locals. With two, a module-level `CLIFF =
        # 12` landed in locals while my_strategy's __globals__ stayed the
        # globals dict, so the name was invisible inside the function and every
        # module-level constant or helper raised NameError.
        #
        # Merging them is safe ONLY because globals(), vars() and locals() are
        # unreachable from bot code: a bot that could call globals() could
        # assign over _getattr_ or __builtins__ in the very dict the guards live
        # in. RestrictedPython rejects leading-underscore names at compile time,
        # which closes the direct route; the indirect one stays closed only
        # while those three builtins stay absent. Both halves are pinned by
        # tests in tests/sandbox/test_bot_api.py.
        exec(byte_code, restricted_globals)
        strategy_fn = restricted_globals.get("my_strategy")
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
        # caller, which knows the lap and car it happened to. The latch in
        # the finally still outranks it.
        raise
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

        # The latch, on *every* way out of a decision: a normal return, a
        # forfeit, a bot error, the net's own exception. Raising from finally
        # supersedes whatever was propagating or being returned, which is
        # exactly the ordering wanted -- a fired net means unbounded time was
        # consumed, and that outranks every other verdict including a forfeit.
        #
        # This lives here rather than in each except branch because the two
        # bugs found in review were both a branch that forgot to ask. There
        # is no branch to forget now: the only way past this line is for the
        # net not to have fired.
        if wall_state["fired"]:
            raise DecisionTimeout(
                f"a decision exceeded the {timeout_ms}ms wall-clock limit"
            ) from None


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
