from backend.sandbox.runner import STRATEGY_TEMPLATE, execute_strategy


def test_default_template_returns_a_valid_decision(sample_state, sample_car):
    result = execute_strategy(STRATEGY_TEMPLATE, sample_state, sample_car)
    assert "error" not in result, result
    assert isinstance(result["pit"], bool)
    assert result["compound"] in {"SOFT", "MEDIUM", "HARD", "INTERMEDIATE", "WET"}
