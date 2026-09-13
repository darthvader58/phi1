"""JSON logs with a match id that follows a match across processes.

A match is now created by an API replica, executed by a worker, and streamed
by (possibly) a different API replica. The only question anyone asks of these
logs is "what happened to match X", and answering it requires the id on every
line rather than in a heading someone has to scroll back to find.
"""

import contextlib
import contextvars
import json
import logging
import sys
from datetime import datetime, timezone

_match_id: contextvars.ContextVar[str] = contextvars.ContextVar("match_id", default="")

# A ContextVar rather than a thread-local: the API is async, so one thread
# serves many concurrent requests and a thread-local would leak one match's id
# onto another's log lines.
#
# ContextVar alone does not make that safe everywhere, though. It is only
# copied into a NEW context: asyncio.to_thread() and each asyncio Task get a
# fresh copy of the caller's context, but a plain ThreadPoolExecutor (what
# loop.run_in_executor() uses, and what a worker's task pool is) does not --
# its threads are long-lived OS threads that keep whatever context they last
# ran with. That cuts both ways: a bound id set by the async caller will NOT
# be visible inside run_in_executor, and if the executor function binds its
# own id without clearing it, a later task reusing that same pooled thread
# will inherit it. Use match_context() (below) for any unit of work that may
# run on a pooled executor thread -- it pairs bind and clear so neither can be
# forgotten.


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str):
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "service": self.service,
            "logger": record.name,
            "message": record.getMessage(),
        }
        match_id = _match_id.get()
        if match_id:
            payload["match_id"] = match_id
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


# Third-party loggers that log routine activity at INFO. Before this module
# raises the root level to INFO, their effective level resolved to root's
# previous default (WARNING) and that traffic was silent. Raising root to
# INFO for our own code's benefit would otherwise also unmute every request
# uvicorn.access logs, every topology event pymongo logs, and every retry
# urllib3 logs -- none of which anyone asks "what happened to match X" about.
# Pinned to WARNING here, explicitly, rather than left to inherit root's
# level, so a future change to root's level cannot silently unmute them
# again.
_NOISY_THIRD_PARTY_LOGGERS = (
    "uvicorn.access",
    "uvicorn.error",
    "pymongo",
    "urllib3",
)


def configure_logging(service: str) -> None:
    """Install the JSON formatter on the root logger, once.

    Idempotent because a module re-import would otherwise add a second handler
    and every line would appear twice — which looks like a retry loop in logs.
    """
    root = logging.getLogger()
    for handler in list(root.handlers):
        if isinstance(handler.formatter, JsonFormatter):
            handler.formatter.service = service
            return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(service))
    root.handlers = [handler]
    root.setLevel(logging.INFO)
    for name in _NOISY_THIRD_PARTY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


def bind_match(match_id: str) -> None:
    _match_id.set(match_id)


def clear_match() -> None:
    _match_id.set("")


@contextlib.contextmanager
def match_context(match_id: str):
    """Bind a match id for the duration of a unit of work.

    Prefer this to bare bind_match/clear_match. A pooled executor reuses OS
    threads and each thread keeps its own context, so an id left bound by one
    unit of work is visible to the next one that runs on that thread. Pairing
    them in a context manager makes that impossible to forget -- including on
    an exception, when a bare clear_match() call would otherwise be skipped.
    """
    token = _match_id.set(match_id)
    try:
        yield
    finally:
        _match_id.reset(token)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
