"""What a player's bot is actually allowed to write.

The containment tests next door prove what the sandbox REFUSES. Nothing proved
what it PERMITS, and four ordinary Python constructs turned out to be broken:
augmented assignment, star-args, module-level names, and catching an exception
by class. A sandbox that rejects every program passes a containment suite
perfectly, so these tests exist as the other half of that contract.

The security tests at the bottom are not decoration. Two of the fixes are only
safe because of a property that lives elsewhere in the file, and a comment
cannot fail a build:

  * merging the exec globals and locals is safe ONLY because globals(), vars()
    and locals() are unreachable, so a bot cannot write into the namespace the
    guards live in;
  * exposing exception classes is safe ONLY as long as BaseException stays out
    of reach, because the operation budget and the wall-clock net are raised as
    BaseExceptions specifically so `except Exception` cannot swallow them.
"""

import pytest

from backend.determinism.budget import BudgetForfeit
from backend.sandbox.runner import ALLOWED_BUILTINS, execute_strategy

STATE = {
    "lap": 1, "total_laps": 10, "track": "bahrain", "weather": "dry",
    "safety_car": False, "safety_car_laps_left": 0, "track_temp": 30.0,
    "cars": [],
}
CAR = {
    "car_id": "c1", "player_id": "p1", "position": 1, "gap_to_leader": 0.0,
    "compound": "HARD", "tyre_age": 1, "fuel_kg": 50.0, "pit_count": 0,
    "pit_laps": [], "last_lap_time": 90.0, "total_time": 90.0, "retired": False,
    "drs_available": False, "compounds_used": ["HARD"], "beliefs": {},
}


def run(body: str, **kw):
    """Run a bot whose my_strategy body is `body`, indented for us."""
    indented = "\n".join("    " + line for line in body.strip().splitlines())
    code = f"def my_strategy(state, my_car):\n{indented}\n"
    return execute_strategy(code, STATE, CAR, seed=42, slot=0, **kw)


def run_module(code: str, **kw):
    """Run a bot given as a whole module, for module-level constructs."""
    return execute_strategy(code, STATE, CAR, seed=42, slot=0, **kw)


def decided(result):
    """True when the bot returned a decision rather than erroring."""
    return "error" not in result


# ─── The four constructs that were broken ────────────────────────────

@pytest.mark.parametrize("op,expr,expected", [
    ("+=", "y += 2", True), ("-=", "y -= 2", False), ("*=", "y *= 2", True),
    ("//=", "y //= 2", False), ("%=", "y %= 2", False), ("**=", "y **= 2", True),
])
def test_augmented_assignment_works(op, expr, expected):
    """_inplacevar_ received the operator as a string and called it."""
    result = run(f"y = 4\n{expr}\nreturn {{'pit': y > 2, 'compound': 'HARD'}}")
    assert decided(result), f"{op} failed: {result}"
    assert result["pit"] is expected


def test_augmented_assignment_on_a_list_does_not_mutate_shared_state():
    """Value semantics: `a += b` is `a = a + b`, never a.__iadd__(b).

    Going through __iadd__ would hand control to whatever object a bot has a
    reference to; the non-in-place operators never invoke it.
    """
    result = run(
        "a = [1]\n"
        "b = a\n"
        "a += [2]\n"
        "return {'pit': len(b) == 1, 'compound': 'HARD'}"
    )
    assert decided(result), result
    assert result["pit"] is True, "the alias saw a mutation; __iadd__ was used"


def test_star_args_works():
    """_apply_ was absent, so every f(*args) raised NameError."""
    result = run("z = max(*[1, 5, 3])\nreturn {'pit': z == 5, 'compound': 'HARD'}")
    assert decided(result), result
    assert result["pit"] is True


def test_double_star_kwargs_works():
    result = run(
        "d = dict(**{'pit': True, 'compound': 'SOFT'})\n"
        "return {'pit': d['pit'], 'compound': d['compound']}"
    )
    assert decided(result), result
    assert result == {"pit": True, "compound": "SOFT"}


def test_module_level_constant_is_visible_inside_my_strategy():
    """exec used separate globals and locals, so module names vanished."""
    result = run_module(
        "CLIFF = 12\n"
        "def my_strategy(state, my_car):\n"
        "    return {'pit': CLIFF > 5, 'compound': 'HARD'}\n"
    )
    assert decided(result), result
    assert result["pit"] is True


def test_module_level_helper_function_is_callable():
    result = run_module(
        "def threshold():\n"
        "    return 7\n"
        "def my_strategy(state, my_car):\n"
        "    return {'pit': threshold() > 5, 'compound': 'HARD'}\n"
    )
    assert decided(result), result
    assert result["pit"] is True


def test_my_strategy_can_recurse():
    result = run_module(
        "def my_strategy(state, my_car, depth=0):\n"
        "    if depth < 2:\n"
        "        return my_strategy(state, my_car, depth + 1)\n"
        "    return {'pit': True, 'compound': 'SOFT'}\n"
    )
    assert decided(result), result
    assert result["pit"] is True


@pytest.mark.parametrize("exc,raiser", [
    ("Exception", "1 / 0"),
    ("ArithmeticError", "1 / 0"),
    ("ZeroDivisionError", "1 / 0"),
    ("ValueError", "int('nope')"),
    ("TypeError", "len(1)"),
    ("KeyError", "{'a': 1}['b']"),
    ("IndexError", "[1][5]"),
    ("LookupError", "[1][5]"),
    ("RuntimeError", "_raise_runtime()"),
])
def test_exceptions_can_be_caught_by_class(exc, raiser):
    """No exception class was importable, forcing every bot into bare except.

    That matters beyond ergonomics: a bare except is exactly the construct that
    swallows the wall-clock signal, so the sandbox was pushing authors toward
    the one pattern the resource limits least want to see.
    """
    if raiser == "_raise_runtime()":
        result = run(
            "try:\n"
            "    raise RuntimeError('x')\n"
            f"except {exc}:\n"
            "    return {'pit': True, 'compound': 'SOFT'}\n"
            "return {'pit': False, 'compound': 'HARD'}"
        )
    else:
        result = run(
            "try:\n"
            f"    x = {raiser}\n"
            f"except {exc}:\n"
            "    return {'pit': True, 'compound': 'SOFT'}\n"
            "return {'pit': False, 'compound': 'HARD'}"
        )
    assert decided(result), f"{exc} unusable: {result}"
    assert result["pit"] is True, f"{exc} did not catch {raiser}"


def test_exception_instance_is_bindable_and_readable():
    result = run(
        "try:\n"
        "    x = 1 / 0\n"
        "except ZeroDivisionError as e:\n"
        "    return {'pit': len(str(e)) > 0, 'compound': 'SOFT'}\n"
        "return {'pit': False, 'compound': 'HARD'}"
    )
    assert decided(result), result
    assert result["pit"] is True


def test_a_bot_can_raise_and_catch_its_own_exception():
    result = run(
        "try:\n"
        "    raise ValueError('planned')\n"
        "except ValueError:\n"
        "    return {'pit': True, 'compound': 'SOFT'}\n"
        "return {'pit': False, 'compound': 'HARD'}"
    )
    assert decided(result), result
    assert result["pit"] is True


# ─── Security: why the above is safe ─────────────────────────────────

@pytest.mark.parametrize("name", ["globals", "vars", "locals", "dir", "eval",
                                  "exec", "compile", "open", "type", "setattr",
                                  "getattr", "hasattr", "__import__"])
def test_namespace_introspection_stays_unreachable(name):
    """The globals/locals merge is safe ONLY while these are unreachable.

    With one shared namespace, a bot that could call globals() could assign
    over _getattr_, _write_ or __builtins__ and take the sandbox apart. Adding
    any of these to ALLOWED_BUILTINS would turn the merge into an escape, so
    this asserts the precondition rather than trusting a comment.
    """
    assert name not in ALLOWED_BUILTINS
    result = run(f"g = {name}\nreturn {{'pit': False, 'compound': 'HARD'}}")
    assert not decided(result), f"{name} is reachable from bot code"
    # Leading-underscore names die at compile time; the rest at lookup.
    assert "NameError" in result["error"] or "invalid variable name" in result["error"]


@pytest.mark.parametrize("guard", ["_getattr_", "_write_", "_getitem_",
                                   "_getiter_", "_inplacevar_", "_apply_",
                                   "__builtins__"])
def test_guard_names_cannot_be_assigned(guard):
    """RestrictedPython rejects leading-underscore names at compile time."""
    result = run_module(
        f"{guard} = None\n"
        "def my_strategy(state, my_car):\n"
        "    return {'pit': False, 'compound': 'HARD'}\n"
    )
    assert not decided(result), f"a bot overwrote {guard}"


def test_base_exception_is_not_reachable():
    """BaseException must stay out of reach.

    The operation budget and the wall-clock net are raised as BaseExceptions
    precisely so `except Exception` cannot swallow them. Exposing BaseException
    would hand bots the ability to decline their own resource limits.
    """
    assert "BaseException" not in ALLOWED_BUILTINS
    # The raiser matters: Python only resolves the exception name when
    # something actually raises, so `try: x = 1` would pass this test on a
    # sandbox that exposed BaseException freely.
    result = run(
        "try:\n"
        "    x = 1 / 0\n"
        "except BaseException:\n"
        "    return {'pit': True, 'compound': 'SOFT'}\n"
        "return {'pit': False, 'compound': 'HARD'}"
    )
    assert not decided(result)
    assert "NameError" in result["error"]


def test_except_exception_cannot_swallow_a_budget_forfeit():
    """The whole point of BudgetForfeit being a BaseException."""
    code = (
        "def my_strategy(state, my_car):\n"
        "    try:\n"
        "        y = 0\n"
        "        while True:\n"
        "            y += 1\n"
        "    except Exception:\n"
        "        return {'pit': False, 'compound': 'HARD'}\n"
        "    return {'pit': False, 'compound': 'HARD'}\n"
    )
    with pytest.raises(BudgetForfeit):
        execute_strategy(code, STATE, CAR, seed=42, slot=0, max_ops=2000)


@pytest.mark.parametrize("attr", ["__subclasses__", "__class__", "__bases__",
                                  "__mro__", "__globals__", "__dict__"])
def test_exception_classes_are_not_a_route_to_introspection(attr):
    """Exception is a class; classes carry the usual escape ladder."""
    result = run(
        f"x = Exception.{attr}\n"
        "return {'pit': False, 'compound': 'HARD'}"
    )
    assert not decided(result), f"Exception.{attr} was reachable"


def test_exception_classes_cannot_be_reached_by_string_lookup():
    """The runtime-string route that defeated the sandbox once before.

    Note Exception.get is NOT an error: safer_getattr returns None for a
    missing attribute rather than raising. That is harmless — None carries no
    reach — and is pre-existing behaviour, so this pins the routes that
    actually lead somewhere instead.
    """
    for probe in ("state.get('__class__')", "my_car.get('__class__')"):
        result = run(f"x = {probe}\nreturn {{'pit': False, 'compound': 'HARD'}}")
        assert not decided(result), f"{probe} was reachable"


def test_inplacevar_rejects_an_unknown_operator():
    """The dispatch table is an allowlist, not a passthrough."""
    from backend.sandbox.runner import _guarded_inplacevar

    with pytest.raises(ValueError):
        _guarded_inplacevar("@=", 1, 2)


# ─── The mro escape, and the two layers behind it ────────────────────

@pytest.mark.parametrize("cls", ["Exception", "ValueError", "int", "str",
                                 "dict", "list", "tuple", "bool"])
def test_mro_is_not_readable_on_any_class_a_bot_can_name(cls):
    """`Exception.mro()[1]` was BaseException, and that was an escape.

    safer_getattr blocks leading-underscore names and INSPECT_ATTRIBUTES;
    `mro` is in neither, being an ordinary public method on every type. A bot
    holding BaseException could raise past the operation budget's forfeit
    latch and past RaceEngine's per-car handler — voiding anyone's match on
    demand, recorded as an engine fault rather than as the player's doing.
    """
    result = run(f"x = {cls}.mro()\nreturn {{'pit': False, 'compound': 'HARD'}}")
    assert not decided(result), f"{cls}.mro() is reachable"
    assert "mro" in result["error"]


def test_classes_can_still_be_called_even_though_their_attributes_are_refused():
    """The guard must not break the reason exception classes were exposed."""
    result = run(
        "try:\n"
        "    raise ValueError('planned')\n"
        "except ValueError as e:\n"
        "    return {'pit': len(str(e)) > 0, 'compound': 'SOFT'}\n"
        "return {'pit': False, 'compound': 'HARD'}"
    )
    assert decided(result), result
    assert result["pit"] is True


def test_a_swallowed_forfeit_still_counts_when_the_bot_raises_a_baseexception():
    """Defence in depth: the latch must hold even if BaseException leaks again.

    Exercised directly rather than through a bot, because the mro route that
    made BaseException reachable is now closed — the point is that closing it
    is not the only thing standing between a bot and a declined budget.
    """
    from backend.determinism.budget import run_with_budget

    def swallow_then_raise():
        try:
            n = 0
            while True:
                n += 1
        except BaseException:
            pass
        raise BaseException("no forfeit for me")

    with pytest.raises(BudgetForfeit):
        run_with_budget(swallow_then_raise, (), max_ops=2000)


def test_builtins_are_copied_into_each_bots_globals():
    """A bot's globals must not hold the module-level dict by reference.

    The exec namespace is now shared with bot module-level code, so a
    reference here would mean one future gap poisons every later bot in the
    process rather than just the one that found it.
    """
    from backend.sandbox.runner import build_sandbox_globals

    g = build_sandbox_globals(seed=1, slot=0)
    assert g["__builtins__"] is not ALLOWED_BUILTINS
    assert g["__builtins__"] == ALLOWED_BUILTINS


def test_print_is_usable_and_silent():
    """print was in the builtins but unusable: _print_ was never installed."""
    result = run("print('hello')\nreturn {'pit': True, 'compound': 'SOFT'}")
    assert decided(result), result
    assert result["pit"] is True
