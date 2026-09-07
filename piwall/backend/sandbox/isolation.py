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

# The child exits with this code when a bot swallowed the per-decision
# wall-clock signal and kept running. Nothing in-process can stop a loop that
# catches every exception, so runner.py leaves by the one door a bare
# `except:` cannot cover. Distinctive enough not to collide with an ordinary
# interpreter exit code.
WALL_CLOCK_VOID_EXITCODE = 87

# Set only inside the isolated child, by _child_entrypoint. runner.py checks
# it before it is willing to take the process down: outside the child -- in
# the API process, or under pytest -- exiting would kill something that is
# not ours to kill.
IN_ISOLATED_CHILD = False

# Child exception type names that mean "the bot breached a limit" rather than
# "our engine has a bug". The text is composed here, from our own constants,
# and never from the child's message: everything crossing that pipe passed
# through untrusted code and may carry filesystem paths.
_LIMIT_EXCEPTION_REASONS = {
    "DecisionTimeout": "your bot exceeded the per-decision wall-clock limit",
}


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
    """Each limit as (name, soft, hard). Soft and hard differ only for CPU.

    At the soft CPU limit the kernel sends SIGXCPU, whose default action
    terminates the child and which _classify_death can tell apart from an
    out-of-memory kill. Only at the hard limit does it send SIGKILL. Setting
    both to the same value skips SIGXCPU in practice, so a bot that merely
    looped forever died by SIGKILL and was reported to its author as having
    exhausted memory — on Linux only, which is why a macOS test run cannot
    see it: there RLIMIT_AS is rejected and the CPU path never competes.
    """
    memory_bytes = memory_mb * 1024 * 1024
    return [
        ("RLIMIT_AS", memory_bytes, memory_bytes),
        ("RLIMIT_CPU", cpu_seconds, cpu_seconds + 1),
        ("RLIMIT_NOFILE", 64, 64),
        ("RLIMIT_FSIZE", 0, 0),
        ("RLIMIT_NPROC", 0, 0),
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
    for name, soft, hard in _limit_plan(memory_mb, cpu_seconds):
        limit = getattr(resource, name, None)
        if limit is None:
            applied[name] = False
            continue
        try:
            resource.setrlimit(limit, (soft, hard))
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
    global IN_ISOLATED_CHILD
    IN_ISOLATED_CHILD = True
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
        # Reached only once the CPU soft limit has had its chance to raise
        # SIGXCPU above, so an out-of-memory kill is the explanation that
        # remains. This holds only because _limit_plan leaves the CPU hard
        # limit above the soft one; with the two equal, a runaway loop
        # arrives here too and is misreported as memory exhaustion.
        return f"your bot exceeded the {memory_mb}MB memory limit"
    return None


def _classify_death(exitcode: Optional[int], memory_mb: int, cpu_seconds: int) -> MatchAborted:
    if exitcode is not None and exitcode < 0:
        reason = _limit_signal_reason(-exitcode, memory_mb, cpu_seconds)
        if reason is not None:
            return LimitExceeded(reason)
        return ChildFailed(f"child terminated by signal {-exitcode}")
    if exitcode == WALL_CLOCK_VOID_EXITCODE:
        # Not a crash: runner.py chose this exit because the bot was ignoring
        # every exception raised at it. See WALL_CLOCK_VOID_EXITCODE.
        return LimitExceeded(
            "your bot ignored the per-decision wall-clock limit"
        )
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
    reason = _LIMIT_EXCEPTION_REASONS.get(payload["type"])
    if reason is not None:
        # A voided match is the bot's doing, not ours. Reporting it as
        # ChildFailed put it down the channel that exists for engine bugs,
        # where it was indistinguishable from one.
        raise LimitExceeded(reason)
    raise ChildFailed(payload["text"])
