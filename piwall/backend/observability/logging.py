"""JSON logs with a match id that follows a match across processes.

A match is now created by an API replica, executed by a worker, and streamed
by (possibly) a different API replica. The only question anyone asks of these
logs is "what happened to match X", and answering it requires the id on every
line rather than in a heading someone has to scroll back to find.
"""

import contextvars
import json
import logging
import sys
from datetime import datetime, timezone

_match_id: contextvars.ContextVar[str] = contextvars.ContextVar("match_id", default="")

# A ContextVar rather than a thread-local: the API is async, so one thread
# serves many concurrent requests and a thread-local would leak one match's id
# onto another's log lines.


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


def bind_match(match_id: str) -> None:
    _match_id.set(match_id)


def clear_match() -> None:
    _match_id.set("")


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
