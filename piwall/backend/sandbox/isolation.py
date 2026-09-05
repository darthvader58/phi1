"""Out-of-process execution with enforced resource limits.

The in-process signal timeout in runner.py cannot arm, because signal.signal()
only works on the main thread of the main interpreter and every call site runs
in a threadpool. A child process has its own main thread, so limits apply.

The spawn start method is mandatory: fork would inherit the parent's MongoDB
connections and memory into untrusted code.
"""

import multiprocessing as mp
import resource
import signal
from typing import Any, Callable, Optional, Tuple

DEFAULT_MEMORY_MB = 512
DEFAULT_CPU_SECONDS = 30
DEFAULT_WALL_SECONDS = 60


class MatchAborted(Exception):
    """The isolated child produced no result.

    Kept as the shared base so existing `except MatchAborted` sites still
    catch both outcomes. Callers that show text to a player must distinguish
    the two subclasses below.
    """


class LimitExceeded(MatchAborted):
    """The child breached a resource limit: CPU, wall clock, memory, disk.

    The message is composed here from the limit that was hit and never from
    child output, so it is always safe to put in an HTTP body or broadcast to
    spectators. It is also the truthful attribution: the submitted bot caused
    it.
    """


class ChildFailed(MatchAborted):
    """The child failed for a reason that is not a limit breach.

    str() is deliberately generic. This text reaches a 400/500 body and the
    spectator WebSocket, where race/[id] renders it verbatim to unauthenticated
    viewers, and the underlying failure is ours rather than the player's -- an
    engine KeyError, or a pickling error carrying absolute filesystem paths.
    The raw child text is kept on .detail for server-side logging only.
    """

    GENERIC_MESSAGE = "The match could not be completed because of an internal error."

    def __init__(self, detail: str):
        super().__init__(self.GENERIC_MESSAGE)
        self.detail = detail


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
        # The type travels separately from the text: the parent classifies on
        # the type and never has to parse the (untrusted, possibly path-laden)
        # message to decide whether it may be shown.
        conn.send(("error", {"type": type(exc).__name__,
                             "text": f"{type(exc).__name__}: {exc}"}))
    finally:
        conn.close()


# A child that breached one of these limits is killed by the kernel before it
# can report anything, so the signal is the only evidence of what happened.
def _limit_signal_reason(sig: int, memory_mb: int, cpu_seconds: int) -> Optional[str]:
    if sig == getattr(signal, "SIGXCPU", None):
        return f"your bot exceeded the {cpu_seconds}s CPU limit"
    if sig == getattr(signal, "SIGXFSZ", None):
        return "your bot tried to write to the filesystem, which is not permitted"
    if sig == getattr(signal, "SIGKILL", None):
        # Nothing else kills the child outright once the parent has stopped
        # waiting: an out-of-memory kill is the remaining explanation.
        return f"your bot exceeded the {memory_mb}MB memory limit"
    return None


def _classify_death(exitcode: Optional[int], memory_mb: int, cpu_seconds: int) -> MatchAborted:
    if exitcode is not None and exitcode < 0:
        reason = _limit_signal_reason(-exitcode, memory_mb, cpu_seconds)
        if reason is not None:
            return LimitExceeded(reason)
        return ChildFailed(f"child terminated by signal {-exitcode}")
    return ChildFailed(f"child exited with code {exitcode} without reporting")


def run_isolated(
    fn: Callable[..., Any],
    args: Tuple = (),
    memory_mb: int = DEFAULT_MEMORY_MB,
    cpu_seconds: int = DEFAULT_CPU_SECONDS,
    wall_seconds: int = DEFAULT_WALL_SECONDS,
) -> Any:
    """Run fn(*args) in a limited child process and return its result.

    fn must be a module-level function: the spawn context re-imports it.
    Raises LimitExceeded on a resource-limit breach and ChildFailed on any
    other child failure; both subclass MatchAborted.
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
            status, payload = "timeout", None
    except EOFError:
        status, payload = "died", None
    finally:
        receiver.close()
        # Reap before reading exitcode: a limit breach shows up only as the
        # signal that killed the child.
        process.join(timeout=1)
        if process.is_alive():
            process.kill()
            process.join()

    if status == "ok":
        return payload
    if status == "timeout":
        raise LimitExceeded(f"your bot exceeded the {wall_seconds}s wall-clock limit")
    if status == "died":
        raise _classify_death(process.exitcode, memory_mb, cpu_seconds)
    if payload["type"] == "MemoryError":
        # Where RLIMIT_AS is enforced (Linux), the breach surfaces as an
        # ordinary exception rather than a signal.
        raise LimitExceeded(f"your bot exceeded the {memory_mb}MB memory limit")
    raise ChildFailed(payload["text"])
