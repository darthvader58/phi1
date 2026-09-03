import pytest

from backend.sandbox.isolation import (
    MatchAborted,
    probe_supported_limits,
    run_isolated,
)


def _add(a, b):
    return a + b


def _spin_forever():
    while True:
        pass


def _eat_memory():
    blob = []
    while True:
        blob.append(bytearray(10_000_000))


def _write_a_file():
    with open("/tmp/piwall_should_not_exist", "w") as handle:
        handle.write("x" * 1000)
    return "wrote"


def test_returns_the_child_result():
    assert run_isolated(_add, (2, 3)) == 5


def test_infinite_loop_is_killed():
    with pytest.raises(MatchAborted):
        run_isolated(_spin_forever, (), cpu_seconds=1, wall_seconds=5)


def test_memory_exhaustion_is_contained():
    with pytest.raises(MatchAborted):
        run_isolated(_eat_memory, (), memory_mb=128, cpu_seconds=10, wall_seconds=20)


def test_child_cannot_write_files():
    with pytest.raises(MatchAborted):
        run_isolated(_write_a_file, (), wall_seconds=10)


def test_the_platform_limit_mechanism_is_visible():
    """Assert what this platform genuinely enforces, rather than assuming.

    RLIMIT_CPU and RLIMIT_FSIZE work everywhere we run. RLIMIT_AS works on
    Linux but is rejected by macOS, so it is reported rather than required:
    on macOS memory is bounded by the CPU and wall-clock limits instead.
    """
    supported = probe_supported_limits()
    assert supported["RLIMIT_CPU"] is True
    assert supported["RLIMIT_FSIZE"] is True
    assert "RLIMIT_AS" in supported
