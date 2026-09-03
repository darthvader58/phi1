from backend.sandbox.validation import MAX_SOURCE_BYTES, validate_submission


def test_accepts_the_default_template():
    from backend.sandbox.runner import STRATEGY_TEMPLATE
    assert validate_submission(STRATEGY_TEMPLATE) is None


def test_rejects_forbidden_names():
    code = "def my_strategy(state, my_car):\n    return getattr(state, 'lap')\n"
    error = validate_submission(code)
    assert error is not None and "getattr" in error


def test_rejects_oversized_source():
    code = "def my_strategy(state, my_car):\n    return {}\n" + ("# pad\n" * 200000)
    error = validate_submission(code)
    assert error is not None and "too large" in error


def test_rejects_code_without_my_strategy():
    code = "def other(state, my_car):\n    return {}\n"
    error = validate_submission(code)
    assert error is not None and "my_strategy" in error


def test_rejects_syntax_errors():
    assert validate_submission("def my_strategy(:\n") is not None


def test_max_source_bytes_is_reasonable():
    assert 10_000 <= MAX_SOURCE_BYTES <= 200_000
