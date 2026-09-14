"""Logs must be machine-readable and carry a match id across processes.

With two API replicas and a worker, the question asked of logs is always "what
happened to match X" — and that cannot be answered by grepping interleaved
plain text from three processes.
"""

import concurrent.futures
import json
import logging

from backend.observability.logging import (
    _match_id,
    bind_match,
    clear_match,
    configure_logging,
    get_logger,
    match_context,
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


def test_reconfiguring_with_a_new_service_name_updates_emitted_records():
    """A worker importing this module after the API configured it must not
    silently keep logging every line as "api". configure_logging("test")
    followed by configure_logging("other") must relabel subsequent records as
    "other", with the handler count still at one.

    Deliberately does not go through _emit(): that helper's own first line is
    configure_logging("test"), which would immediately stomp the "other" name
    this test is trying to observe.
    """
    configure_logging("test")
    configure_logging("other")
    root = logging.getLogger()
    handler = root.handlers[0]
    records = []

    class Capture(logging.Handler):
        def emit(self, record):
            records.append(handler.format(record))

    cap = Capture()
    root.addHandler(cap)
    try:
        get_logger("piwall.test").info("hello")
    finally:
        root.removeHandler(cap)

    payload = json.loads(records[-1])
    assert payload["service"] == "other"
    json_handlers = [h for h in root.handlers if h.formatter.__class__.__name__ == "JsonFormatter"]
    assert len(json_handlers) == 1
    configure_logging("test")  # restore for any tests that run after this one


def test_thread_pool_reuse_does_not_leak_match_id_between_tasks():
    """A single-worker ThreadPoolExecutor reuses one OS thread for every task
    submitted to it -- which is exactly how a worker's task pool runs units of
    work. If task A binds a match id and leaves it bound, task B queued to the
    same pool afterward would otherwise inherit A's id for as long as it runs
    before binding its own -- silently mislabeling B's log lines as A's match.
    match_context pairs bind and clear so that id cannot outlive task A.
    """
    seen = {}

    def task_a():
        with match_context("match-A"):
            pass  # id is bound only for the lifetime of this block

    def task_b():
        # Must observe the pool's thread state left behind by task_a, before
        # binding anything of its own.
        seen["before_bind"] = _match_id.get()
        with match_context("match-B"):
            pass

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(task_a).result()
        pool.submit(task_b).result()

    assert seen["before_bind"] == ""
