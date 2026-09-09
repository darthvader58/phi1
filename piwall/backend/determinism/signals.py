"""Control signals the engine raises through untrusted bot code.

Three times in this codebase a handler has absorbed a signal it did not know
about, and each time the failure was silent:

  * a fix caught ``BaseException`` and turned a fired wall-clock net into an
    ordinary forfeit, so a match completed instead of voiding;
  * a ``except BudgetForfeit: raise`` branch re-raised without consulting the
    wall-clock latch, so a match completed instead of voiding;
  * a per-car handler caught ``BaseException`` and swallowed ``DecisionTimeout``,
    so a match completed instead of voiding.

The common cause was not carelessness. It was that ``BudgetForfeit``,
``_WallClockFired`` and ``DecisionTimeout`` were flat siblings of
``BaseException`` with no shared vocabulary, so *every* handler had to
independently remember which ones it was allowed to absorb. Prose in a
docstring cannot enforce that, and a name or module string test only guesses
at it.

This module makes the contract a type. A signal declares what must happen to
it, and handlers dispatch on that declaration instead of on a list they have
to keep in their heads:

    BaseException
    └── SandboxSignal          out of reach of bot code's `except Exception`
        ├── RecordedOutcome    the engine absorbs it and writes it to the replay
        └── MatchVoiding       the engine must NEVER absorb it; the match voids

A new signal inherits correct handling by choosing a base class. Choosing
neither is a test failure, not a production incident -- see
``tests/sandbox/test_signal_contract.py``, which walks every BaseException
subclass defined under ``backend/`` and requires it to be classified.
"""


class SandboxSignal(BaseException):
    """Base for every control signal raised through bot code.

    Derives from BaseException rather than Exception so that a bot writing
    ``except Exception:`` -- or the bare ``except:`` the sandbox used to force
    on people -- cannot intercept its own resource limits.
    """


class RecordedOutcome(SandboxSignal):
    """An outcome the engine absorbs and writes into the replay.

    The match continues. The signal is a verdict about one decision, not about
    the match, and it must be reproducible: anything deriving from this has to
    land in the same place on every machine, or replays diverge.
    """


class MatchVoiding(SandboxSignal):
    """A signal that must reach the match runner. The match is void.

    Never catch this to record a decision. Handlers that catch broadly must
    re-raise it explicitly, and the reason is that the conditions these
    describe are not reproducible -- elapsed time, an exhausted process
    rlimit. Recording one as an outcome would write a machine-speed-dependent
    result into a replay that is supposed to be byte-identical everywhere.

    Voiding is not a failure mode to be avoided; it is the correct answer when
    the alternative is a replay that does not reproduce.
    """


# Signals that belong to neither the engine nor the bot: the operator pressed
# Ctrl-C, the interpreter is shutting down, a generator is being finalised.
#
# They are BaseException-derived and so reach the same broad handlers, but the
# right answer is neither "record it" nor "void the match" — it is to get out
# of the way. Absorbing a KeyboardInterrupt as a bot error meant Ctrl-C during
# an in-process race was swallowed once per car per lap and the race carried
# on, which is a genuinely unpleasant thing to debug.
#
# Handlers that catch broadly must re-raise these ahead of their catch-all.
INTERPRETER_SIGNALS = (KeyboardInterrupt, SystemExit, GeneratorExit)
