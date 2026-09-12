"""Logs must be machine-readable and carry a match id across processes.

With two API replicas and a worker, the question asked of logs is always "what
happened to match X" — and that cannot be answered by grepping interleaved
plain text from three processes.
"""

import json
import logging

from backend.observability.logging import (
    bind_match,
    clear_match,
    configure_logging,
    get_logger,
)


def _emit(caplog, fn):
    """Run fn and return the single formatted record as a dict."""
    configure_logging("test")
    logger = get_logger("piwall.test")
    handler = logger.handlers[0] if logger.handlers else logging.getLogger().handlers[0]
    records = []

    class Capture(logging.Handler):
        def emit(self, record):
            records.append(handler.format(record))

    root = logging.getLogger()
    cap = Capture()
    root.addHandler(cap)
    try:
        fn(logger)
    finally:
        root.removeHandler(cap)
    assert records, "nothing was logged"
    return json.loads(records[-1])


def test_records_are_json(caplog):
    payload = _emit(caplog, lambda log: log.info("hello"))
    assert payload["message"] == "hello"
    assert payload["level"] == "INFO"


def test_records_name_the_service(caplog):
    payload = _emit(caplog, lambda log: log.info("hello"))
    assert payload["service"] == "test"


def test_records_carry_a_timestamp(caplog):
    payload = _emit(caplog, lambda log: log.info("hello"))
    assert payload["timestamp"]


def test_a_bound_match_id_appears_on_every_record(caplog):
    def emit(log):
        bind_match("m_abc")
        log.info("running")

    try:
        payload = _emit(caplog, emit)
        assert payload["match_id"] == "m_abc"
    finally:
        clear_match()


def test_match_id_is_absent_when_nothing_is_bound(caplog):
    clear_match()
    payload = _emit(caplog, lambda log: log.info("no match here"))
    assert "match_id" not in payload


def test_clear_match_removes_the_binding(caplog):
    bind_match("m_abc")
    clear_match()
    payload = _emit(caplog, lambda log: log.info("after clear"))
    assert "match_id" not in payload


def test_exceptions_are_serialized_not_dropped(caplog):
    def emit(log):
        try:
            raise ValueError("boom")
        except ValueError:
            log.exception("failed")

    payload = _emit(caplog, emit)
    assert "ValueError" in payload["exception"]
    assert "boom" in payload["exception"]


def test_configure_logging_is_idempotent(caplog):
    """Called once per process, but a re-import must not double every line."""
    configure_logging("test")
    configure_logging("test")
    root = logging.getLogger()
    json_handlers = [h for h in root.handlers if h.formatter.__class__.__name__ == "JsonFormatter"]
    assert len(json_handlers) == 1
