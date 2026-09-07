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
from backend.sandbox.match_job import _make_user_strategy
from backend.sandbox.runner import (
    DEFAULT_DECISION_WALL_MS, STRATEGY_TEMPLATE, execute_strategy,
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


def test_the_builtin_bots_leave_two_orders_of_magnitude_of_headroom():
    """DEFAULT_DECISION_OPS is only defensible if real bots stay far below it.

    Measured over a full race rather than a single synthetic call: EQL-44's
    cost scales with laps remaining and field size, so its worst decision is
    on lap 1, not on an arbitrary one.
    """
    worst = {}

    def counted(bot_id, fn):
        def strategy(state, my_car):
            value, ops = run_with_budget(fn, (state, my_car),
                                         DEFAULT_DECISION_OPS)
            worst[bot_id] = max(worst.get(bot_id, 0), ops)
            return value
        return strategy

    engine = RaceEngine(track=_track(total_laps=78), seed=7)
    for i, (bot_id, spec) in enumerate(BUILTIN_BOTS.items()):
        engine.add_car(bot_id, f"p{i}", counted(bot_id, spec["strategy"]),
                       i + 1, spec["starting_compound"])
    engine.run()

    busiest = max(worst.values())
    assert busiest * 100 < DEFAULT_DECISION_OPS, (
        f"built-in bots are close to the budget: {worst}"
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
