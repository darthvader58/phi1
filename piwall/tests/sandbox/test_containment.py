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
    result = execute_strategy(GETATTR, sample_state, sample_car)
    assert "error" in result


def test_hasattr_builtin_is_unreachable(sample_state, sample_car):
    result = execute_strategy(HASATTR, sample_state, sample_car)
    assert "error" in result


WRITE = (
    "def my_strategy(state, my_car):\n"
    "    my_car.position = 1\n"
    "    return {'pit': False, 'compound': 'SOFT'}\n"
)


def test_user_code_cannot_mutate_passed_state(sample_state, sample_car):
    result = execute_strategy(WRITE, sample_state, sample_car)
    assert "error" in result
