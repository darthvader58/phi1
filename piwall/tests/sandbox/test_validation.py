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


def test_accepts_forbidden_words_as_variable_names():
    """`dir`, `input`, `vars`, `open` are ordinary names.

    The check matched every ast.Name, assignment targets included, so a first
    submission using `dir = 1` was rejected with "Use of 'dir' is not allowed"
    and no way to understand why.
    """
    code = (
        "def my_strategy(state, my_car):\n"
        "    dir = 1\n"
        "    input = state.lap\n"
        "    vars = my_car.tyre_age\n"
        "    del vars\n"
        "    return {'pit': False, 'compound': my_car.compound}\n"
    )
    assert validate_submission(code) is None


def test_still_rejects_reading_a_forbidden_name():
    for call in ("getattr(state, 'lap')", "eval('1')", "open('/etc/passwd')",
                 "globals()", "__import__('os')"):
        code = f"def my_strategy(state, my_car):\n    return {call}\n"
        assert validate_submission(code) is not None, call


def test_shadowing_a_forbidden_name_does_not_unlock_reading_another():
    code = (
        "def my_strategy(state, my_car):\n"
        "    dir = 1\n"
        "    return getattr(state, 'lap')\n"
    )
    error = validate_submission(code)
    assert error is not None and "getattr" in error
