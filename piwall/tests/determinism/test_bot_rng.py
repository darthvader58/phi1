import random
import pytest
from backend.sandbox.runner import build_sandbox_globals

FORBIDDEN = ["time", "datetime", "os", "uuid", "secrets", "sys", "socket"]


def _stream(seed, slot):
    return build_sandbox_globals(seed=seed, slot=slot)["random"]


def test_each_slot_gets_its_own_generator():
    assert _stream(42, 0) is not _stream(42, 1)


def test_the_module_itself_is_never_injected():
    """The global module makes every bot share one stream."""
    assert _stream(42, 0) is not random


def test_same_seed_and_slot_reproduce_the_same_draws():
    a = [_stream(42, 0).random() for _ in range(5)]
    b = [_stream(42, 0).random() for _ in range(5)]
    assert a == b


def test_different_slots_draw_independently():
    assert [_stream(42, 0).random() for _ in range(5)] != [_stream(42, 1).random() for _ in range(5)]


def test_different_seeds_draw_differently():
    assert [_stream(42, 0).random() for _ in range(5)] != [_stream(43, 0).random() for _ in range(5)]


def test_one_bot_draining_its_stream_cannot_perturb_another():
    """The failure the shared module caused: draw order leaking across slots."""
    quiet = _stream(42, 1)
    baseline = [quiet.random() for _ in range(3)]

    noisy, quiet_again = _stream(42, 0), _stream(42, 1)
    for _ in range(1000):
        noisy.random()
    assert [quiet_again.random() for _ in range(3)] == baseline


@pytest.mark.parametrize("name", FORBIDDEN)
def test_nondeterministic_modules_are_unreachable(name):
    assert name not in build_sandbox_globals(seed=42, slot=0)
