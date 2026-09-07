from backend.sandbox.runner import execute_strategy

GETATTR = (
    "def my_strategy(state, my_car):\n"
    "    getattr(state, 'lap')\n"
    "    return {'pit': False, 'compound': 'SOFT'}\n"
)
HASATTR = (
    "def my_strategy(state, my_car):\n"
    "    hasattr(state, 'lap')\n"
    "    return {'pit': False, 'compound': 'SOFT'}\n"
)


def test_getattr_builtin_is_unreachable(sample_state, sample_car):
    result = execute_strategy(GETATTR, sample_state, sample_car, seed=42, slot=0)
    assert "error" in result


def test_hasattr_builtin_is_unreachable(sample_state, sample_car):
    result = execute_strategy(HASATTR, sample_state, sample_car, seed=42, slot=0)
    assert "error" in result


WRITE = (
    "def my_strategy(state, my_car):\n"
    "    my_car.position = 1\n"
    "    return {'pit': False, 'compound': 'SOFT'}\n"
)


def test_user_code_cannot_mutate_passed_state(sample_state, sample_car):
    result = execute_strategy(WRITE, sample_state, sample_car, seed=42, slot=0)
    assert "error" in result


# ─── Attribute access smuggled in as a runtime string ─────────────────

# RestrictedPython rewrites attribute *syntax*, so `state.__class__` is a
# compile error. It cannot see a name that only exists as a string at
# runtime, and Namespace maps subscription and .get() onto getattr. That made
# `state.get('__class__')` attribute access the rewriter never inspected, and
# from the class it is three hops to the real builtins:
#
#     g = state.get('__class__').get          # the unbound Namespace.get
#     imp = g(g, '__globals__')['__builtins__']['__import__']
#     g(imp('sys'), 'settrace')(None)         # counted budget switched off
#
# Every step of that chain now has to get past a guard.

DUNDER_VIA_GET = (
    "def my_strategy(state, my_car):\n"
    "    return {'pit': bool(state.get('__class__')), 'compound': 'SOFT'}\n"
)
DUNDER_VIA_SUBSCRIPT = (
    "def my_strategy(state, my_car):\n"
    "    return {'pit': bool(state['__class__']), 'compound': 'SOFT'}\n"
)
DUNDER_VIA_CAR_GET = (
    "def my_strategy(state, my_car):\n"
    "    return {'pit': bool(my_car.get('__class__')), 'compound': 'SOFT'}\n"
)
DUNDER_VIA_NESTED_SUBSCRIPT = (
    "def my_strategy(state, my_car):\n"
    "    return {'pit': bool(my_car.beliefs['__class__']), 'compound': 'SOFT'}\n"
)


def test_get_refuses_a_dunder_name(sample_state, sample_car):
    result = execute_strategy(DUNDER_VIA_GET, sample_state, sample_car,
                              seed=42, slot=0)
    assert "error" in result


def test_subscription_refuses_a_dunder_name(sample_state, sample_car):
    result = execute_strategy(DUNDER_VIA_SUBSCRIPT, sample_state, sample_car,
                              seed=42, slot=0)
    assert "error" in result


def test_the_car_namespace_refuses_a_dunder_name_too(sample_state, sample_car):
    result = execute_strategy(DUNDER_VIA_CAR_GET, sample_state, sample_car,
                              seed=42, slot=0)
    assert "error" in result


def test_a_nested_namespace_refuses_a_dunder_key(sample_state, sample_car):
    result = execute_strategy(DUNDER_VIA_NESTED_SUBSCRIPT, sample_state,
                              sample_car, seed=42, slot=0)
    assert "error" in result


LEGITIMATE_ACCESS = (
    "def my_strategy(state, my_car):\n"
    "    belief = my_car.beliefs.get('c2', {})\n"
    "    leader = state.cars[0]\n"
    "    lap = state['lap']\n"
    "    gain = belief.get('undercut_gain', 0)\n"
    "    missing = my_car.get('no_such_field', 'MEDIUM')\n"
    "    pit = gain > 2.0 and lap > 0 and leader.tyre_age > 0\n"
    "    return {'pit': pit, 'compound': missing}\n"
)


def test_the_guards_do_not_block_ordinary_field_access(sample_state, sample_car):
    """No RaceState or CarState field starts with "_" -- checked against
    engine/serialize.py -- so refusing those names costs bots nothing."""
    result = execute_strategy(LEGITIMATE_ACCESS, sample_state, sample_car,
                              seed=42, slot=0)
    assert result == {"pit": True, "compound": "MEDIUM"}
