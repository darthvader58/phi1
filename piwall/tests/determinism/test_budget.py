import pytest
from backend.determinism.budget import (
    BudgetForfeit, DEFAULT_DECISION_OPS, run_with_budget,
)


def _cheap(n):
    return n * 2


def _expensive(n):
    total = 0
    for i in range(100000):
        total += i
    return total


def test_a_cheap_call_returns_normally():
    result, ops = run_with_budget(_cheap, (21,), max_ops=1000)
    assert result == 42
    assert ops > 0


def test_op_count_is_reproducible():
    """The whole point: the penalty must not depend on machine speed."""
    first = run_with_budget(_cheap, (21,), max_ops=1000)[1]
    for _ in range(20):
        assert run_with_budget(_cheap, (21,), max_ops=1000)[1] == first


def test_exceeding_the_budget_forfeits():
    with pytest.raises(BudgetForfeit):
        run_with_budget(_expensive, (0,), max_ops=500)


def test_the_forfeit_point_is_reproducible():
    """Same code, same budget, same place — every time."""
    counts = []
    for _ in range(10):
        try:
            run_with_budget(_expensive, (0,), max_ops=500)
        except BudgetForfeit as exc:
            counts.append(exc.ops_used)
    assert len(set(counts)) == 1, f"forfeit point varied: {sorted(set(counts))}"


def test_default_budget_is_a_positive_int():
    assert isinstance(DEFAULT_DECISION_OPS, int) and DEFAULT_DECISION_OPS > 0


# ─── The forfeit as a race event, not a crash ─────────────────────────

import sys

from backend.engine.bots import BUILTIN_BOTS
from backend.engine.physics import TrackPhysics, TyreModel
from backend.engine.race import RaceEngine
from backend.sandbox import runner
from backend.sandbox.match_job import _make_user_strategy
from backend.sandbox.runner import (
    DEFAULT_DECISION_WALL_MS, STRATEGY_TEMPLATE, DecisionTimeout,
    execute_strategy,
)

RUNAWAY = (
    "def my_strategy(state, my_car):\n"
    "    x = 0\n"
    "    while True:\n"
    "        x = x + 1\n"
)

# A bot that catches its own forfeit and returns anyway. CPython drops the
# trace function as soon as the tracer raises, so the counter cannot trip a
# second time -- the verdict has to survive on the recorded flag alone.
SWALLOWER = (
    "def my_strategy(state, my_car):\n"
    "    try:\n"
    "        x = 0\n"
    "        while x < 100000:\n"
    "            x = x + 1\n"
    "    except:\n"
    "        pass\n"
    "    return {'pit': True, 'compound': 'SOFT'}\n"
)


# EQL-44 is the busiest built-in strategy, rewritten the way a player would
# have to write it: same algorithm, returning dicts, since bots cannot import
# the engine's Decision. Used to price a realistic bot on the sandboxed path.
EQL_44_AS_PLAYER_CODE = """
def my_strategy(state, my_car):
    remaining = state.total_laps - state.lap
    if remaining <= 3 or my_car.tyre_age < 5:
        return {"pit": False, "compound": my_car.compound}

    deg_rates = {"SOFT": 0.10, "MEDIUM": 0.065, "HARD": 0.045}
    current_rate = deg_rates.get(my_car.compound, 0.06)
    pit_delta = 22.0

    current_cost = sum(
        current_rate * (my_car.tyre_age + k) ** 1.1
        for k in range(1, remaining + 1)
    )

    best_alt = None
    best_alt_cost = float("inf")
    for comp in deg_rates:
        rate = deg_rates[comp]
        if comp == my_car.compound and my_car.pit_count == 0:
            continue
        alt_cost = pit_delta + sum(
            rate * k ** 1.1 for k in range(1, remaining + 1)
        )
        if alt_cost < best_alt_cost:
            best_alt_cost = alt_cost
            best_alt = comp

    if best_alt is None:
        return {"pit": False, "compound": my_car.compound}

    delta_ev = current_cost - best_alt_cost

    if delta_ev > 0:
        should_pit_now = True
        for offset in range(1, min(4, remaining)):
            future_remaining = remaining - offset
            if future_remaining <= 0:
                break
            future_cost = sum(
                current_rate * (my_car.tyre_age + offset + k) ** 1.1
                for k in range(1, future_remaining + 1)
            )
            future_rate = deg_rates.get(best_alt, 0.06)
            future_alt = pit_delta + sum(
                future_rate * k ** 1.1 for k in range(1, future_remaining + 1)
            )
            if future_cost - future_alt > delta_ev * 1.05:
                should_pit_now = False
                break

        for rival in state.cars:
            if rival.car_id == my_car.car_id or rival.retired:
                continue
            if rival.position > my_car.position:
                belief = my_car.beliefs.get(rival.car_id, {})
                if belief.get("undercut_viable", False):
                    should_pit_now = True
                    break

        if should_pit_now:
            return {"pit": True, "compound": best_alt}

    return {"pit": False, "compound": my_car.compound}
"""


def _track(total_laps=20):
    models = {
        comp: TyreModel(compound=comp, alpha=a, k=k, e=e, base_lap_time=92.0)
        for comp, a, k, e in (
            ("SOFT", 0.0, 0.09, 1.15),
            ("MEDIUM", 0.5, 0.055, 1.10),
            ("HARD", 1.0, 0.035, 1.05),
            ("INTERMEDIATE", 3.5, 0.03, 1.05),
            ("WET", 6.0, 0.03, 1.05),
        )
    }
    return TrackPhysics(
        name="bahrain", base_lap_time=92.0, pit_loss_seconds=22.0,
        total_laps=total_laps, drs_zones=3, overtake_difficulty=0.5,
        fuel_load_kg=110.0, tyre_models=models,
    )


def _race_with_a_runaway_bot(seed=42, max_ops=500):
    engine = RaceEngine(track=_track(), seed=seed)
    engine.add_car(
        "USR-01", "p1", _make_user_strategy(RUNAWAY, seed, 0, max_ops=max_ops),
        1, "MEDIUM",
    )
    engine.add_car(
        "VEL-01", "bot", BUILTIN_BOTS["VEL-01"]["strategy"], 2, "SOFT",
    )
    return engine.run()


def test_a_forfeiting_bot_yields_a_no_op_and_the_race_continues():
    """A forfeit must not abort the match — it is a recorded non-decision."""
    result = _race_with_a_runaway_bot()

    forfeits = [e for e in result.events if e.event_type == "budget_forfeit"]
    assert forfeits, "no budget_forfeit event was recorded"
    assert all(e.car_id == "USR-01" for e in forfeits)

    # The race ran to the end and the forfeiting car took no action.
    assert result.total_laps == 20
    assert len(result.final_standings) == 2
    forfeiter = next(c for c in result.final_standings if c.car_id == "USR-01")
    assert forfeiter.pit_count == 0
    assert forfeiter.pit_laps == []


def test_the_forfeit_lands_on_the_same_laps_every_run():
    """The replay-safety claim, checked at the level the replay records."""
    runs = [
        tuple(e.lap for e in _race_with_a_runaway_bot().events
              if e.event_type == "budget_forfeit")
        for _ in range(5)
    ]
    assert len(set(runs)) == 1, f"forfeit laps varied: {sorted(set(runs))}"
    assert len(runs[0]) == 20, "every lap's decision should have forfeited"


def test_a_bot_that_catches_its_forfeit_still_forfeits(sample_state, sample_car):
    """The budget is not a suggestion a bot can decline."""
    with pytest.raises(BudgetForfeit):
        execute_strategy(SWALLOWER, sample_state, sample_car,
                         seed=42, slot=0, max_ops=500)


def test_an_honest_strategy_is_nowhere_near_the_budget(sample_state, sample_car):
    result = execute_strategy(STRATEGY_TEMPLATE, sample_state, sample_car,
                              seed=42, slot=0)
    assert "error" not in result


def test_a_sandboxed_bot_stays_well_inside_the_budget():
    """DEFAULT_DECISION_OPS is only defensible against the real cost of a bot.

    Measured on the path a player's code actually takes -- compiled by
    RestrictedPython and run with the sandbox's guards in place -- not by
    calling a built-in bot directly. `safer_getattr`, `_guarded_getitem` and
    `_inplacevar_` are Python, so every guarded access is itself traced and
    billed to the bot. Calling EQL-44 directly costs ~950 operations on its
    worst lap; the same algorithm as sandboxed player code costs ~3,140.
    Measuring the cheap path would have overstated the headroom threefold.

    Measured over a whole race, and over the biggest race there is: EQL-44's
    cost scales with laps remaining and field size, so its worst decision is
    lap 1 of Monaco with a full grid, not an arbitrary one.
    """
    worst = 0
    real_run_with_budget = runner.run_with_budget

    def spy(fn, args=(), max_ops=DEFAULT_DECISION_OPS):
        nonlocal worst
        value, ops = real_run_with_budget(fn, args, max_ops)
        worst = max(worst, ops)
        return value, ops

    runner.run_with_budget = spy
    try:
        engine = RaceEngine(track=_track(total_laps=78), seed=7)
        for slot in range(10):
            engine.add_car(
                f"USR-{slot}", f"p{slot}",
                _make_user_strategy(EQL_44_AS_PLAYER_CODE, 7, slot),
                slot + 1, "MEDIUM",
            )
        engine.run()
    finally:
        runner.run_with_budget = real_run_with_budget

    assert worst > 0, "the spy never saw a decision"
    assert worst * 20 < DEFAULT_DECISION_OPS, (
        f"a realistic bot costs {worst} ops against a "
        f"{DEFAULT_DECISION_OPS} budget -- less than 20x headroom"
    )


def test_the_wall_clock_net_sits_far_above_the_budget():
    """The two limits must not compete to decide a race.

    Burning the whole budget in ordinary Python costs tens of milliseconds;
    the net is seconds. Whichever machine this runs on, the counter trips
    first for any bot whose cost is in executed lines.
    """
    import time

    start = time.perf_counter()
    with pytest.raises(BudgetForfeit):
        run_with_budget(_expensive, (0,), DEFAULT_DECISION_OPS)
    elapsed_ms = (time.perf_counter() - start) * 1000

    assert elapsed_ms * 10 < DEFAULT_DECISION_WALL_MS, (
        f"a full budget costs {elapsed_ms:.0f}ms against a "
        f"{DEFAULT_DECISION_WALL_MS}ms net"
    )


def test_the_trace_function_is_restored(sample_state, sample_car):
    """settrace is global state: leaking it would break coverage and debuggers."""
    before = sys.gettrace()
    execute_strategy(STRATEGY_TEMPLATE, sample_state, sample_car,
                     seed=42, slot=0)
    assert sys.gettrace() is before

    with pytest.raises(BudgetForfeit):
        execute_strategy(RUNAWAY, sample_state, sample_car,
                         seed=42, slot=0, max_ops=500)
    assert sys.gettrace() is before


# The budget is only a budget if a bot cannot switch it off. This ran a full
# match at 400,000 ops per decision -- twice DEFAULT_DECISION_OPS -- with
# forfeits == 0, by reaching sys.settrace through a dunder name passed as a
# runtime string. See tests/sandbox/test_containment.py for the guards.
UNHOOKS_THE_TRACER = (
    "def my_strategy(state, my_car):\n"
    "    g = state.get('__class__').get\n"
    "    imp = g(g, '__globals__')['__builtins__']['__import__']\n"
    "    g(imp('sys'), 'settrace')(None)\n"
    "    x = 0\n"
    "    while x < 400000:\n"
    "        x = x + 1\n"
    "    return {'pit': True, 'compound': 'SOFT'}\n"
)


def test_a_bot_cannot_switch_off_its_own_budget():
    engine = RaceEngine(track=_track(), seed=42)
    engine.add_car(
        "USR-01", "p1",
        _make_user_strategy(UNHOOKS_THE_TRACER, 42, 0, max_ops=500),
        1, "MEDIUM",
    )
    engine.add_car("VEL-01", "bot", BUILTIN_BOTS["VEL-01"]["strategy"], 2, "SOFT")
    result = engine.run()

    # Either it forfeits or the guard refuses it outright -- but it must never
    # get to run 400,000 operations against a 500-operation budget and then
    # have its decision honoured.
    forfeiter = next(c for c in result.final_standings if c.car_id == "USR-01")
    assert forfeiter.pit_count == 0, "the escaping bot's pit decision was honoured"


def test_the_tracer_survives_a_bot_that_tries_to_unhook_it(sample_state, sample_car):
    before = sys.gettrace()
    result = execute_strategy(UNHOOKS_THE_TRACER, sample_state, sample_car,
                              seed=42, slot=0, max_ops=500)
    assert "error" in result
    assert sys.gettrace() is before


SWALLOWS_THEN_CRASHES = (
    "def my_strategy(state, my_car):\n"
    "    try:\n"
    "        x = 0\n"
    "        while x < 100000:\n"
    "            x = x + 1\n"
    "    except:\n"
    "        pass\n"
    "    return {'pit': 1 / 0, 'compound': 'SOFT'}\n"
)


def test_a_swallowed_forfeit_is_recorded_even_if_the_bot_then_crashes(
        sample_state, sample_car):
    """Otherwise a forfeiting bot is indistinguishable from a buggy one.

    The Decision is a no-op either way, so determinism survives -- but with
    no budget_forfeit event the replay says "this bot threw an exception",
    which is not what happened.
    """
    with pytest.raises(BudgetForfeit):
        execute_strategy(SWALLOWS_THEN_CRASHES, sample_state, sample_car,
                         seed=42, slot=0, max_ops=500)


def test_a_forfeit_that_crashes_still_reaches_the_replay():
    engine = RaceEngine(track=_track(), seed=42)
    engine.add_car(
        "USR-01", "p1",
        _make_user_strategy(SWALLOWS_THEN_CRASHES, 42, 0, max_ops=500),
        1, "MEDIUM",
    )
    result = engine.run()
    forfeits = [e for e in result.events if e.event_type == "budget_forfeit"]
    assert len(forfeits) == 20, "a swallowed forfeit went unrecorded"


# The wall-clock net fires inside the protected block -- hidden C work, few
# line events, so the op budget does not trip there -- and the bare `except:`
# eats it. Execution then continues into unprotected Python that DOES trip
# the budget, so a BudgetForfeit propagates out of a decision during which
# the net had already fired.
SWALLOWS_THE_NET_THEN_FORFEITS = (
    "def my_strategy(state, my_car):\n"
    "    try:\n"
    "        total = 0\n"
    "        for i in range(200):\n"
    "            total = total + len(sorted(range(500000)))\n"
    "    except:\n"
    "        pass\n"
    "    y = 0\n"
    "    while True:\n"
    "        y = y + 1\n"
    "    return {'pit': True, 'compound': 'SOFT'}\n"
)


def test_a_fired_net_outranks_a_later_forfeit(sample_state, sample_car):
    """If the net fired, the decision voids -- whatever propagates afterwards.

    A forfeit is a recorded, replay-safe verdict; a fired net means unbounded
    time was consumed and the match must be voided instead. Letting the
    forfeit escape first turned the second into the first, and the match
    completed.
    """
    with pytest.raises(DecisionTimeout):
        execute_strategy(SWALLOWS_THE_NET_THEN_FORFEITS, sample_state,
                         sample_car, 100, seed=42, slot=0, max_ops=5000)


def test_a_race_voids_rather_than_completing_when_the_net_fired():
    """The property at the level that matters: the match does not finish."""
    engine = RaceEngine(track=_track(total_laps=3), seed=42)
    engine.add_car(
        "USR-01", "p1",
        _make_user_strategy(SWALLOWS_THE_NET_THEN_FORFEITS, 42, 0,
                            max_ops=5000, timeout_ms=100),
        1, "MEDIUM",
    )
    with pytest.raises(DecisionTimeout):
        engine.run()
