"""Reproducible cost accounting for a single bot decision.

Wall-clock timeouts cannot bound a bot here: if the same bot times out on
lap 30 on one machine and lap 31 on another, the two replays diverge and the
determinism contract is broken. Counting executed lines instead gives a
budget whose exhaustion point depends only on the code and its inputs, so
the penalty lands in exactly the same place on every machine.

Wall-clock limits still exist one layer out, in sandbox/isolation.py. Those
are a safety net against a hang, and when they fire the match is voided and
requeued rather than completed (spec 5.5).
"""

import sys
from typing import Any, Callable, Tuple

# Roughly two orders of magnitude above the busiest built-in strategy, so a
# genuine bot never approaches it and a runaway loop always trips it.
DEFAULT_DECISION_OPS = 200_000


class BudgetForfeit(BaseException):
    """Raised when a decision exhausts its operation budget.

    Deliberately derived from BaseException rather than Exception. The
    budget bounds *untrusted* code, and untrusted code is free to write
    `try: ... except Exception: pass` around its own loop. An Exception
    subclass would be swallowed there, and because CPython clears the trace
    function as soon as a trace callback raises, the counter would never get
    a second chance to trip -- the budget would be a suggestion. Every call
    site in this repo catches BudgetForfeit by name, so it never escapes as
    an unhandled BaseException.
    """

    def __init__(self, ops_used: int, max_ops: int):
        super().__init__(f"decision exhausted its {max_ops}-operation budget")
        self.ops_used = ops_used
        self.max_ops = max_ops


def run_with_budget(
    fn: Callable[..., Any],
    args: Tuple = (),
    max_ops: int = DEFAULT_DECISION_OPS,
) -> Tuple[Any, int]:
    """Run fn(*args) under a counted-line budget.

    Returns (result, ops_used). Raises BudgetForfeit at the exact same line
    on every machine, which is what makes the penalty replay-safe.

    The count is of CPython "line" trace events, so it is stable for a given
    interpreter version but not necessarily identical across versions: the
    compiler is free to emit a different number of line events for the same
    source. That is why DEFAULT_DECISION_OPS sits orders of magnitude above
    any real strategy rather than being tuned tight against one.
    """
    state = {"ops": 0, "tripped": False}

    def tracer(frame, event, arg):
        if event == "line":
            state["ops"] += 1
            if state["ops"] > max_ops:
                # CPython drops the trace function the moment this raises, so
                # this is the counter's only shot: see the note on the flag
                # below for what happens if the bot swallows it.
                state["tripped"] = True
                raise BudgetForfeit(state["ops"], max_ops)
        return tracer

    previous = sys.gettrace()
    sys.settrace(tracer)
    try:
        result = fn(*args)
    finally:
        sys.settrace(previous)

    if state["tripped"]:
        # fn returned normally after the budget was exhausted, which means it
        # caught the forfeit. Untraced execution followed, so ops is a floor
        # rather than a total -- but the verdict stands.
        raise BudgetForfeit(state["ops"], max_ops)

    return result, state["ops"]
