"""Out-of-process execution with enforced resource limits.

The in-process signal timeout in runner.py cannot arm, because signal.signal()
only works on the main thread of the main interpreter and every call site runs
in a threadpool. A child process has its own main thread, so limits apply.

The spawn start method is mandatory: fork would inherit the parent's MongoDB
connections and memory into untrusted code.
"""

import multiprocessing as mp
import resource
from typing import Any, Callable, Tuple

DEFAULT_MEMORY_MB = 512
DEFAULT_CPU_SECONDS = 30
DEFAULT_WALL_SECONDS = 60


class MatchAborted(Exception):
    """The isolated child exceeded its limits or failed."""


def _limit_plan(memory_mb: int, cpu_seconds: int):
    return [
        ("RLIMIT_AS", memory_mb * 1024 * 1024),
        ("RLIMIT_CPU", cpu_seconds),
        ("RLIMIT_NOFILE", 64),
        ("RLIMIT_FSIZE", 0),
        ("RLIMIT_NPROC", 0),
    ]


def _apply_limits(memory_mb: int, cpu_seconds: int) -> dict:
    """Apply each limit independently; return which ones actually took effect.

    Not every rlimit exists on every platform. macOS rejects RLIMIT_AS and
    RLIMIT_DATA outright ("ValueError: current limit exceeds maximum limit"),
    so applying the set as one block would abort every match on a developer
    machine. Each limit is applied on its own and the outcome recorded, so
    callers can assert on the mechanism rather than assume it.
    """
    applied = {}
    for name, value in _limit_plan(memory_mb, cpu_seconds):
        limit = getattr(resource, name, None)
        if limit is None:
            applied[name] = False
            continue
        try:
            resource.setrlimit(limit, (value, value))
            applied[name] = True
        except (ValueError, OSError):
            applied[name] = False
    return applied


def probe_supported_limits(memory_mb: int = 512, cpu_seconds: int = 30) -> dict:
    """Report which limits this platform accepts, without running user code.

    Linux enforces RLIMIT_AS, so memory is bounded directly. macOS does not,
    and there memory exhaustion is bounded only by the CPU and wall-clock
    limits. Production runs on Linux (spec 3, managed microVM); this function
    exists so the gap is visible in tests rather than assumed away.
    """
    return _child_probe_limits(memory_mb, cpu_seconds)


def _child_probe_limits(memory_mb: int, cpu_seconds: int) -> dict:
    return run_isolated(_apply_limits, (memory_mb, cpu_seconds),
                        memory_mb=memory_mb, cpu_seconds=cpu_seconds,
                        wall_seconds=15)


def _child_entrypoint(fn, args, memory_mb, cpu_seconds, conn) -> None:
    try:
        applied = _apply_limits(memory_mb, cpu_seconds)
        if fn is _apply_limits:
            conn.send(("ok", applied))
        else:
            conn.send(("ok", fn(*args)))
    except BaseException as exc:
        conn.send(("error", f"{type(exc).__name__}: {exc}"))
    finally:
        conn.close()


def run_isolated(
    fn: Callable[..., Any],
    args: Tuple = (),
    memory_mb: int = DEFAULT_MEMORY_MB,
    cpu_seconds: int = DEFAULT_CPU_SECONDS,
    wall_seconds: int = DEFAULT_WALL_SECONDS,
) -> Any:
    """Run fn(*args) in a limited child process and return its result.

    fn must be a module-level function: the spawn context re-imports it.
    Raises MatchAborted on timeout, limit breach, or child failure.
    """
    ctx = mp.get_context("spawn")
    receiver, sender = ctx.Pipe(duplex=False)
    process = ctx.Process(
        target=_child_entrypoint,
        args=(fn, args, memory_mb, cpu_seconds, sender),
    )
    process.start()
    sender.close()

    try:
        if receiver.poll(wall_seconds):
            status, payload = receiver.recv()
        else:
            status, payload = "error", f"exceeded {wall_seconds}s wall clock"
    except EOFError:
        status, payload = "error", "child died without reporting"
    finally:
        receiver.close()
        process.join(timeout=1)
        if process.is_alive():
            process.kill()
            process.join()

    if status == "ok":
        return payload
    raise MatchAborted(payload)
