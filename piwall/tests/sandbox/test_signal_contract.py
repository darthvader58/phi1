"""Every control signal must declare how handlers are to treat it.

This is the test that makes the signal hierarchy load-bearing rather than
decorative. Three separate times a handler in this codebase absorbed a signal
it did not know about, and each time a match completed instead of voiding —
silently, with a replay that would not reproduce.

The root cause was that the signals were flat siblings of BaseException, so
every handler had to independently remember which ones it could absorb. The
fix is that a signal now declares its own handling by choosing a base class.
This test is what stops the next signal from forgetting to.

The rule it enforces: a class that derives from BaseException but NOT from
Exception bypasses every `except Exception:` in the process — bot code's and
ours. Anything with that reach must say whether the engine records it or must
never touch it.
"""

import importlib
import inspect
import pkgutil

import pytest

import backend
from backend.determinism.signals import (
    MatchVoiding,
    RecordedOutcome,
    SandboxSignal,
)


# Modules that legitimately cannot import in a bare test environment, each
# with the reason. This is an allowlist rather than a blanket `except: skip`
# on purpose: a skip that swallows unknown import errors would let a module
# holding an unclassified signal drop out of the sweep silently, which is the
# same shape of bug this whole file exists to prevent.
KNOWN_UNIMPORTABLE = {
    "backend.data.calibrate_all": "legacy standalone script, imports a 'piwall' package",
    "backend.data.calibrate_bahrain": "legacy standalone script, imports a 'piwall' package",
    "backend.data.debug_data": "legacy standalone script, imports a 'piwall' package",
    "backend.data.calibration": "calibration tooling; numpy/scipy are not runtime deps",
    "backend.data.fastf1_loader": "calibration tooling; fastf1 is not a runtime dep",
}


def _iter_backend_modules():
    """Import every module under backend/ so no signal escapes the sweep.

    A signal defined in a module nobody imports is exactly the one that gets
    forgotten, so this walks the package rather than taking a hand-written
    list of modules to check.
    """
    modules, unexpected = [], []
    for info in pkgutil.walk_packages(backend.__path__, prefix="backend."):
        if ".tests" in info.name:
            continue
        try:
            modules.append(importlib.import_module(info.name))
        except BaseException as exc:
            if info.name not in KNOWN_UNIMPORTABLE:
                unexpected.append(f"{info.name}: {type(exc).__name__}: {exc}")

    assert unexpected == [], (
        "these modules could not be imported, so any control signal they "
        "define was not checked. Fix the import, or add the module to "
        "KNOWN_UNIMPORTABLE with a reason: " + "; ".join(unexpected)
    )
    return modules


def _signal_classes():
    """Every BaseException subclass defined under backend/, deduplicated."""
    found = {}
    for module in _iter_backend_modules():
        for _, obj in vars(module).items():
            if (
                inspect.isclass(obj)
                and issubclass(obj, BaseException)
                and obj.__module__.startswith("backend.")
            ):
                found[f"{obj.__module__}.{obj.__qualname__}"] = obj
    return found


def test_the_sweep_finds_something():
    """A classification test over an empty set passes vacuously."""
    assert _signal_classes(), "no exception classes found under backend/"


def test_every_baseexception_signal_declares_its_handling():
    """The invariant. A new signal that skips this fails here, not in a race."""
    unclassified = []
    for name, cls in sorted(_signal_classes().items()):
        if issubclass(cls, Exception):
            # An ordinary error. `except Exception:` catches it, which is the
            # behaviour every caller already expects; nothing to declare.
            continue
        if cls in (SandboxSignal, RecordedOutcome, MatchVoiding):
            continue  # the vocabulary itself
        if not issubclass(cls, (RecordedOutcome, MatchVoiding)):
            unclassified.append(name)

    assert unclassified == [], (
        "these bypass every `except Exception:` in the process but do not say "
        "how handlers must treat them. Derive from RecordedOutcome (the engine "
        "absorbs it and writes it to the replay) or MatchVoiding (the engine "
        "must never absorb it; the match is void): " + ", ".join(unclassified)
    )


def test_the_two_categories_are_mutually_exclusive():
    """A signal that is both recorded and voiding has no defined handling."""
    both = [
        name for name, cls in _signal_classes().items()
        if issubclass(cls, RecordedOutcome) and issubclass(cls, MatchVoiding)
    ]
    assert both == [], f"signals claiming both categories: {both}"


def test_no_signal_is_catchable_by_bot_code():
    """The reason these derive from BaseException in the first place.

    A bot writing `except Exception:` — or the bare `except:` the sandbox used
    to force on people — must not be able to intercept its own resource
    limits.
    """
    for name, cls in _signal_classes().items():
        if issubclass(cls, SandboxSignal):
            assert not issubclass(cls, Exception), (
                f"{name} is a SandboxSignal but also an Exception, so bot code "
                f"can catch it with `except Exception:`"
            )


def test_the_known_signals_are_classified_as_intended():
    """Pins today's three, so a reparenting shows up as a failure here."""
    from backend.determinism.budget import BudgetForfeit
    from backend.sandbox.runner import DecisionTimeout, _WallClockFired

    assert issubclass(BudgetForfeit, RecordedOutcome)
    assert not issubclass(BudgetForfeit, MatchVoiding)

    assert issubclass(DecisionTimeout, MatchVoiding)
    assert issubclass(_WallClockFired, MatchVoiding)
    assert not issubclass(DecisionTimeout, RecordedOutcome)
    assert not issubclass(_WallClockFired, RecordedOutcome)


def test_a_broad_handler_cannot_absorb_a_voiding_signal():
    """The property the whole hierarchy exists to guarantee, executed.

    Written as real control flow rather than an isinstance assertion, because
    what failed three times was control flow, not a type relationship someone
    got wrong on paper.
    """
    from backend.sandbox.runner import DecisionTimeout

    absorbed_by_exception = False
    try:
        try:
            raise DecisionTimeout("net fired")
        except Exception:
            absorbed_by_exception = True
    except DecisionTimeout:
        pass
    assert not absorbed_by_exception, "`except Exception:` absorbed a voiding signal"


def test_a_recorded_outcome_is_absorbed_by_the_engines_handler_but_not_by_bot_code():
    """RecordedOutcome has the opposite requirement, and it also matters.

    The engine must be able to catch it (that is how a forfeit reaches the
    replay), while bot code must not (that is how a bot would decline its own
    budget). Those two demands are why it sits under BaseException but is
    caught explicitly rather than by a broad clause.
    """
    from backend.determinism.budget import BudgetForfeit

    absorbed_by_exception = False
    try:
        try:
            raise BudgetForfeit(10, 5)
        except Exception:
            absorbed_by_exception = True
    except BudgetForfeit:
        pass
    assert not absorbed_by_exception, "bot code could catch a forfeit"

    caught_explicitly = False
    try:
        raise BudgetForfeit(10, 5)
    except RecordedOutcome:
        caught_explicitly = True
    assert caught_explicitly, "the engine could not catch a forfeit by contract"
